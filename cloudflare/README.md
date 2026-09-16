# LakeWatch Cloudflare sensor

This Worker is LakeWatch's high-frequency state owner. It runs without a
personal computer, checks five verified first-party booking APIs every five
minutes, persists observations and the confirmed state in D1, asks a targeted
GitHub Actions Playwright job to validate candidate inventory, and delivers
confirmed alerts through Cloudflare Queues.

The direct adapters are:

- Ranginui — STAah property inventory
- Grand Suites — Ibex room inventory
- Peppers Bluewater — Accor GraphQL availability
- The Hermitage — Agilysys room rates
- Hahei Beach — Newbook availability HTML

Lakeview Wix and Galaxy's SiteMinder endpoint remain browser-only. The Worker
dispatches one targeted browser job for each of them hourly. All hotel and stay
configuration now lives in the repository-level `hotels.json` file.

## State and notification flow

```text
API observation
  ├─ confirmed transition → D1 event
  └─ candidate transition → targeted GitHub Playwright
                            → authenticated /validation callback
                            → confirmed or rejected D1 event

confirmed D1 event → one Queue delivery per configured channel
                   → Feishu / PushPlus independently retried
```

An actionable candidate never replaces the last confirmed D1 snapshot before
the browser callback succeeds. Repeated candidate observations share one
idempotent pending validation. GitHub Actions is an execution engine only; it
does not own Cloudflare state or send Cloudflare-originated alerts.

## Local verification

```bash
npm ci
npm test
npm run check
```

For a local D1 database:

```bash
npx wrangler d1 execute lakewatch --local --file=./schema.sql
npx wrangler dev --test-scheduled
```

## Fresh Cloudflare setup

1. Log in with `npx wrangler login`.
2. Create D1 and copy its ID into `wrangler.jsonc`:

   ```bash
   npx wrangler d1 create lakewatch
   npx wrangler d1 execute lakewatch --remote --file=./schema.sql
   ```

3. Create the notification Queue and dead-letter Queue:

   ```bash
   npx wrangler queues create lakewatch-notifications
   npx wrangler queues create lakewatch-notifications-dlq
   ```

4. Create a fine-grained GitHub token restricted to this repository with
   `Contents: write`, then save Worker secrets:

   ```bash
   npx wrangler secret put GITHUB_TOKEN
   npx wrangler secret put ADMIN_TOKEN
   npx wrangler secret put VALIDATION_TOKEN
   npx wrangler secret put FEISHU_WEBHOOK_URL
   npx wrangler secret put FEISHU_WEBHOOK_SECRET
   npx wrangler secret put PUSHPLUS_TOKEN
   npx wrangler secret put PUSHPLUS_TOPIC
   ```

   PushPlus 渠道由 `wrangler.jsonc` 的 `PUSHPLUS_CHANNELS` 控制。`wechat,clawbot`
   会分别投递到微信公众号和微信 ClawBot，并各自独立重试。

5. Deploy with `npx wrangler deploy`.
6. In GitHub Actions secrets, configure:
   - `CLOUDFLARE_VALIDATION_URL`, for example
     `https://lakewatch-sensor.<subdomain>.workers.dev/validation`
   - `CLOUDFLARE_VALIDATION_TOKEN`, exactly matching the Worker
     `VALIDATION_TOKEN` secret.

## Existing pre-v2 D1 database

Do not reapply `schema.sql` to an existing database. Back it up, then apply the
one-time additive migration:

```bash
npx wrangler d1 export lakewatch --remote --output lakewatch-before-v2.sql
npx wrangler d1 execute lakewatch --remote --file=./migrate-v2.sql
```

## Shadow and cutover

`SHADOW_MODE` is committed as `true`. In shadow mode the Worker records real
API observations but does not dispatch browsers, enqueue alerts, or send the D1
daily summary. Keep the existing GitHub hourly monitor active while comparing
results for at least seven days.

Cut over only after the comparison is clean:

1. Set the repository Actions variable `CLOUDFLARE_PRIMARY=true`. Scheduled
   legacy browser checks, keepalive commit, and the legacy SQLite daily summary
   will then skip.
   The targeted Cloud-triggered browser validation workflow remains available.
2. Change `SHADOW_MODE` to `false` and deploy.
3. Run the Worker `/run` endpoint once with `Authorization: Bearer <ADMIN_TOKEN>`.
4. Confirm `/health` returns HTTP 200 and Queue deliveries appear in D1.

Rollback is the reverse: set `SHADOW_MODE=true`, deploy, and set
`CLOUDFLARE_PRIMARY=false`.

## Runtime behaviour

- Direct sensors run every five minutes with concurrency three.
- The first failure backs off that hotel for 15 minutes, the second for one
  hour, and later consecutive failures for six hours.
- Candidate validation dispatches are retried after 15 minutes.
- Notification channels have independent delivery records and Queue retries.
- Browser-only hotels run hourly; direct API hotels do not receive an hourly
  full-browser pass.
- Browser-only callbacks carry the originating sensor cycle ID, so a retried
  GitHub job cannot record the same observation twice. Eventless callbacks are
  rejected for direct-API hotels.
- A D1-based daily summary is queued at 00:07 Asia/Shanghai.
- Observations, completed events, deliveries, and cycles are retained for 90
  days.
- `/health` reports sensor, browser-only, validation, and notification health.
  In active mode it returns HTTP 503 if the sensor is stale, either browser-only
  hotel has not reported for 150 minutes, validation is stuck for 45 minutes,
  notification delivery is stuck for two hours, or no notification channel is
  configured. Shadow mode intentionally checks only the sensor path.
- GitHub's `Cloudflare health watchdog` probes `/health` hourly from outside
  Cloudflare. A non-200 response fails the workflow and preserves the response
  as an artifact, so a total Worker/Cron outage is still externally visible.
