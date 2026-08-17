# Lake Tekapo 酒店放房监控（免费接口版）

一个可长期运行的小时级监控服务。它直接检查酒店公开的官网预订页，不使用 SerpApi 或其他付费酒店搜索 API。固定查询 `2027-02-05` 至 `2027-02-06`、2 位成人，并监控：

- Ranginui at Lake Tekapo
- Lakeview Tekapo
- Grand Suites Lake Tekapo
- Galaxy Boutique Hotel
- Peppers Bluewater Resort Lake Tekapo
- The Hermitage Hotel Mt Cook（原需求里的 “Herimage” 按此酒店纠正）

## 提醒规则

- 第一次成功执行只建立基线，不发提醒。
- `无房 → 有房` 时提醒。
- 已有房时出现新房型才提醒。
- 价格变化、持续有房、持续无房均不提醒。
- 官网超时、改版或拦截自动浏览器时记为查询错误，不会误标成无房，也不会覆盖最后一次有效快照。
- 飞书发送失败会保留在 SQLite 待发队列，后续执行自动重试。

所有结果都来自酒店官网或其官方预订引擎；不会因为第三方 OTA 新增同一房型而打扰你。

## 费用

代码和查询本身不需要购买 API。你只需要一台能常驻联网运行 Docker 的电脑、NAS 或服务器。如果用自己现有的 Mac/NAS，软件侧可以做到零新增费用；如果租云服务器，服务器本身可能收费。

## 免费云端运行（推荐）

项目已包含 GitHub Actions 小时任务。使用**公开 GitHub 仓库**和标准
`ubuntu-latest` runner 时不消耗付费 Actions 分钟，Mac 关机也会继续运行。
仓库代码会公开，但 Webhook 和签名密钥只保存在 GitHub Secrets，不会公开。
请注意，公开仓库也会公开酒店名单和入住日期；如果不希望公开这些行程信息，应改用
私有仓库。GitHub Free 私有仓库目前每月包含 2,000 分钟，但小时浏览器任务是否始终
够用取决于每次实际耗时，不能像公开仓库一样保证不限分钟。

1. 在 GitHub 新建一个 **Public** 仓库，例如 `tekapo-hotel-monitor`。
2. 把本项目文件上传到仓库；不要上传本地 `.env`（它已在 `.gitignore` 中）。
3. 打开仓库的 `Settings → Secrets and variables → Actions`，新增两个
   Repository secret：
   - `FEISHU_WEBHOOK_URL`
   - `FEISHU_WEBHOOK_SECRET`
4. 打开仓库 `Actions → Hourly hotel monitor → Run workflow`，手动执行一次。
   第一次成功运行只建立基线，不会发送放房提醒。

之后任务会在每小时 UTC 第 17 分执行。GitHub 定时任务不是实时系统，高峰期可能
延迟几分钟。SQLite 状态会通过 Actions cache 传给下一次任务，失败查询不会覆盖
上一次有效状态；每次 JSONL 执行日志同时作为 Artifact 保存 90 天。

北京时间每天 `00:07` 还会向飞书发送一条简短的前一日汇总，包括执行次数、异常数、
放房变化、提醒次数，以及六家酒店的最新有房/无房数量。

公开仓库如果连续 60 天没有仓库活动，GitHub 会自动停用定时工作流。项目包含一个
每月仅更新一次心跳文件的工作流，以保持小时任务长期启用。

## 启动

以下是本地 Docker 运行方式；选择 GitHub Actions 云端版时不需要执行本节。

1. 在飞书群添加“自定义机器人”，复制 Webhook；如启用签名校验，同时复制签名密钥。飞书推送是可选的，不填写时提醒仍会写入数据库和日志。
2. 复制配置并启动：

```bash
cp .env.example .env
# 编辑 .env；要推送飞书就填写 FEISHU_WEBHOOK_URL
docker compose up -d --build
```

Docker 首次构建会下载 Chromium，因此镜像较大，但没有按次或按月的查询费。服务每小时第 7 分钟（`Pacific/Auckland`）执行，容器启动时也会立即执行一次。

## 状态与日志

- `GET /healthz`：存活检查。
- `GET /status`：下一次执行时间、最后一次执行、6 家酒店快照。
- `GET /runs?limit=24`：每小时执行历史。
- `POST /check`：手动执行；设置 `ADMIN_TOKEN` 后须带 `X-Admin-Token` 请求头。

持久化文件：

- `data/monitor.db`：每次执行、逐酒店观察、最新快照和提醒待发队列。
- `data/monitor.jsonl`：逐行 JSON 执行日志，按 UTC 每日轮转，默认保留 365 天。

常用命令：

```bash
curl http://127.0.0.1:8080/status
curl http://127.0.0.1:8080/runs?limit=24
curl -X POST http://127.0.0.1:8080/check
docker compose logs -f monitor
```

## 本地开发

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python -m playwright install chromium
pytest
```

## 运行注意

- 官网可能改版、验证码或短暂限流。此时该酒店会显示 `error` 并写日志，不会制造放房提醒；后续小时任务会自动再查。
- 请保持每小时一次，不要把频率调得很高，以免给酒店官网造成不必要的请求。
- SQLite 与 JSONL 必须挂载到持久卷（Compose 已配置）。会休眠或无持久磁盘的平台不能保证小时调度和历史日志。
- 自建服务无法向 ChatGPT 客户端反向推送；当前主动提醒通道是飞书 Webhook，并同时保留 HTTP 状态接口。
