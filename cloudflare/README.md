# LakeWatch Cloudflare sensor

This Worker is the lightweight, high-frequency layer of LakeWatch. It runs
without a personal computer, checks five verified first-party booking APIs every
five minutes, stores every observation in D1, and asks GitHub Actions to run a
browser validation when a meaningful change appears.

The current direct adapters are:

- Ranginui — STAah property inventory
- Grand Suites — Ibex room inventory
- Peppers Bluewater — Accor GraphQL availability
- The Hermitage — Agilysys room rates
- Hahei Beach — Newbook availability HTML

Lakeview Wix currently exposes no date-specific bookable service, and Galaxy's
SiteMinder GraphQL endpoint is protected by a browser challenge. They remain in
the hourly Playwright watchdog instead of being guessed from page text.

## Local verification

```bash
npm ci
npm test
npm run check
```

For an end-to-end local D1 run:

```bash
npx wrangler d1 execute lakewatch --local --file=./schema.sql
npx wrangler dev --test-scheduled
curl 'http://127.0.0.1:8787/cdn-cgi/handler/scheduled?cron=*/5+*+*+*+*&format=json'
```

## Cloud setup

1. Log in with `npx wrangler login`.
2. Create the database with `npx wrangler d1 create lakewatch`.
3. Put the returned database ID in `wrangler.jsonc`.
4. Apply the schema with
   `npx wrangler d1 execute lakewatch --remote --file=./schema.sql`.
5. Create a fine-grained GitHub token restricted to this public repository and
   capable of sending `repository_dispatch`, then store it with
   `npx wrangler secret put GITHUB_TOKEN`.
6. Store a random manual-run token with
   `npx wrangler secret put ADMIN_TOKEN`.
7. Store the existing notification credentials with `npx wrangler secret put`:
   `FEISHU_WEBHOOK_URL`, `FEISHU_WEBHOOK_SECRET`, `PUSHPLUS_TOKEN`, and
   optionally `PUSHPLUS_TOPIC`.
8. Deploy with `npx wrangler deploy`.

`SHADOW_MODE` is deliberately `true` in the committed configuration. In shadow
mode the Worker checks and logs real inventory but does not trigger production
browser runs or send notifications. Keep it enabled during the comparison
period. After the results have been compared, switching `SHADOW_MODE` to
`false` enables browser validation and concise Feishu/PushPlus alerts for
confirmed new inventory. Monitoring failures remain in the logs and daily
summary; they never generate a separate immediate alert.

Each hotel has its own persistent error backoff. After the first failed request
it waits 15 minutes before trying that hotel again; a second consecutive failure
waits one hour, and further consecutive failures wait six hours. A successful
response immediately resets the hotel to the normal five-minute frequency.
Other hotels continue on schedule throughout the backoff.
