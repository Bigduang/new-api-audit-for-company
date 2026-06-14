# Token 审计功能当前进度（Ubuntu 部署版）

更新时间：2026-06-02

## 目标

建设一个独立 `token-audit` 审计服务，用于统计 New-API 一段时间内每个用户、每个 token 的用量，并判断请求是否真实用于工作。对疑似非工作或不确定请求，需要能追溯到具体用户、token、模型、时间、tokens 和 prompt 预览。

当前路线是：

- New-API 只加最小 Go hook，异步上报请求事件和用量事件。
- `token-audit` 服务独立部署，使用本机 SQLite 文件库存储。
- 使用 `request_id` 关联 prompt 内容和最终 token 消费记录。
- prompt 原文加密保存，报表默认只展示预览。
- 审计数据默认保留 30 天，分类和报表任务可以在后半夜处理前一天数据。

## 当前完成状态

已完成：

- FastAPI 审计服务骨架。
- HMAC 验签接口：
  - `POST /internal/new-api/audit/request`
  - `POST /internal/new-api/audit/usage`
- `request_id` 双事件合并：
  - request 先到、usage 后到可以合并。
  - usage 先到、request 后到也可以合并。
- 文件型 SQLite 已验证可以自动创建目录、建表、写入，并在服务重启后读取。
- 同一 `request_id` 的并发/重复 usage 上报已做幂等防护。
- 接收端增加 `AUDIT_MAX_BODY_BYTES`，超大 payload 只写 deadletter 标记，不创建请求记录。
- prompt AES-GCM 加密保存。
- 规则分类器：
  - 编码实现
  - 调试修复
  - 架构设计
  - 配置运维
  - 文档编写
  - 代码审查
  - 数据分析
  - 疑似非工作
  - 其他
- DeepSeek LLM 分类器入口，要求模型输出 JSON，并在服务侧做 JSON 解析兜底。
- 用户/token 用量 Markdown 报表。
- 疑似非工作/不确定请求复核清单。
- 企业微信推送入口。
- 人工复核接口。
- SQLAlchemy 自动建表，生产默认 SQLite。
- SQLite WAL / busy timeout 配置。
- 30 天保留清理任务。
- New-API 最小 hook patch。
- 自动化测试。

测试结果：

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider
# 14 passed
```

## 线上服务器探测结果

已通过旧审计脚本中的 SSH 配置连接线上 Ubuntu 主机，未回显密码或业务密钥，只执行只读检查。

环境结论：

- 系统：Ubuntu 24.04.4 LTS，kernel 6.8。
- 资源：2 vCPU，3.6 GiB 内存，约 47 GiB 根分区可用空间。
- 当前负载：load average 约 0.04，容器总内存占用较低。
- Docker：Docker 29.4.3，Docker Compose v5.1.3，buildx 可用。
- Python：Python 3.12.3 可用，Python `sqlite3` 模块内置 SQLite 3.45.1。
- `sqlite3` 命令行工具未安装；不影响服务运行，只影响人工命令行查看数据库。
- cron 和 systemd 可用。
- 当前线上 compose 文件：`/opt/proxy/docker-compose.yml`。
- 当前容器：`new-api`、`cpa`、`nginx`、`postgres`、`redis`。
- 当前 New-API 镜像：`calciumion/new-api:latest`。
- 当前 New-API 数据库：Postgres 15，不需要新增 MySQL。
- 当前 Docker 网络：`proxy_newapi-network`。

判断：

- 这台 VPS 支持当前 SQLite 版 `token-audit` 方案。
- 审计库建议放在 `/opt/token-audit/data/token_audit.db`，默认保留 30 天。
- `token-audit` 建议使用 Docker 部署，并加入 `proxy_newapi-network`；当前项目已补 `Dockerfile`、`deploy/docker-compose.yml` 和启动前自动 migrate 的 entrypoint。
- 由于 New-API 运行在容器内，网络连通有两种方式：
  - Docker Compose 方式：`token-audit` 作为同一个 compose 项目的服务运行，New-API 配置 `AUDIT_ENDPOINT=http://token-audit:8000`。
  - systemd 方式：`token-audit` 运行在宿主机，New-API 服务需要增加 `extra_hosts: ["host.docker.internal:host-gateway"]`，并配置 `AUDIT_ENDPOINT=http://host.docker.internal:8000`。
- New-API 需要替换为 fork 后的自建镜像。服务器具备本地 Docker 构建能力，但 2C/3.6G 不适合频繁构建前端+Bun+Go 镜像，推荐在本机或 GitHub Actions 构建后推送镜像，服务器只拉取运行。
- 当前方案不再读取 New-API 主库做回填；审计任务在次日早晨运行，直接使用 `token-audit` SQLite 中已采集完成的数据。

## 已生成文件

- 审计服务源码：`token_audit/`
- New-API 补丁：`patches/new-api-audit-hook.patch`
- SQLite 默认配置：`.env.example`
- MySQL 建表 SQL：`sql/schema.mysql.sql`（保留为可选参考，当前生产方案不使用）
- 配置样例：`.env.example`
- Python 生产依赖：`requirements.txt`
- Python 测试依赖：`requirements-dev.txt`
- 使用说明：`README.md`
- 测试：`tests/test_service.py`
- 历史脚本保留：`run_audit.py`

## Ubuntu 部署准备

目标服务器为 Ubuntu。建议使用 Ubuntu 22.04 或 24.04。

安装基础依赖：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl sqlite3
```

如果系统 Python 低于 3.12，建议安装 Python 3.12 或使用已有 Conda/pyenv 环境。当前开发环境验证版本为 Python 3.12。

创建部署目录：

```bash
sudo mkdir -p /opt/token-audit
sudo chown -R $USER:$USER /opt/token-audit
mkdir -p /opt/token-audit/data
```

同步项目后安装依赖：

```bash
cd /opt/token-audit
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

本地开发或跑测试时再安装：

```bash
pip install -r requirements-dev.txt
```

生成 prompt 加密密钥：

```bash
python - <<'PY'
import base64, os
print("base64:" + base64.b64encode(os.urandom(32)).decode())
PY
```

创建 `.env`：

```bash
cp .env.example .env
```

关键配置：

```bash
AUDIT_DATABASE_URL=sqlite:////opt/token-audit/data/token_audit.db
AUDIT_SECRET=replace-with-shared-hmac-secret
AUDIT_PROMPT_ENCRYPTION_KEY=base64:replace-with-generated-key
AUDIT_TIMEZONE=Asia/Shanghai
AUDIT_RETENTION_DAYS=30
```

初始化 SQLite 数据库：

```bash
set -a
. ./.env
set +a
python -m token_audit.cli migrate
sqlite3 /opt/token-audit/data/token_audit.db '.tables'
```

如果线上暂时不安装 `sqlite3` 命令行工具，也可以省略最后一行；服务使用 Python 内置 SQLite 驱动。

启动服务：

```bash
uvicorn token_audit.main:app --host 0.0.0.0 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

## Ubuntu systemd 示例

创建 `/etc/systemd/system/token-audit.service`：

```ini
[Unit]
Description=Token Audit Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/token-audit
EnvironmentFile=/opt/token-audit/.env
ExecStart=/opt/token-audit/.venv/bin/uvicorn token_audit.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
User=ubuntu
Group=ubuntu

[Install]
WantedBy=multi-user.target
```

启用服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now token-audit
sudo systemctl status token-audit
```

查看日志：

```bash
journalctl -u token-audit -f
```

## New-API 部分状态

已生成补丁：

```bash
patches/new-api-audit-hook.patch
```

补丁内容：

- 新增 `audit/sender.go`
- 修改 `controller/relay.go`
  - 在请求解析后采集 prompt 审计事件。
- 修改 `model/log.go`
  - 在消费日志记录后采集 usage 审计事件。

New-API 需要自己构建镜像或二进制，不能继续直接使用官方原版镜像。

推荐流程：

```bash
cd /path/to/new-api
git apply /opt/token-audit/patches/new-api-audit-hook.patch
gofmt -w audit/sender.go controller/relay.go model/log.go
go test ./audit ./model ./controller
docker build -t your-registry/new-api:audit .
```

首次部署建议关闭上报：

```bash
AUDIT_ENABLED=false
```

确认 New-API 正常后再开启：

```bash
AUDIT_ENABLED=true
AUDIT_ENDPOINT=http://token-audit:8000
AUDIT_SECRET=replace-with-same-secret-as-token-audit
AUDIT_TIMEOUT_MS=800
AUDIT_QUEUE_SIZE=1000
AUDIT_MAX_EVENT_BYTES=1048576
AUDIT_EXCLUDED_TOKEN_NAMES=audit-classifier
```

注意：当前本机已安装用户态 Go 1.26.1，并完成 `gofmt`、`git diff --check` 和编译型 `go test`。上线前仍建议在 CI 或镜像构建环境里再次执行完整验证。

### Fork 二开进度

已拉取用户 fork：

```bash
git clone https://github.com/Bigduang/new-api-audit.git /home/zczd/projects/agent/new-api-audit
```

当前 fork 基线：

```bash
7aaa5332 2026-06-02 fix(channels): reveal advanced validation errors #5239
```

已创建本地二开分支并提交：

```bash
cd /home/zczd/projects/agent/new-api-audit
git switch audit-hook
git log -1 --oneline
# 728255db feat: add token audit hooks
```

本次改动：

- 新增 `audit/sender.go`
- 修改 `controller/relay.go`
- 修改 `model/log.go`

本机已安装用户态 Go 工具链：

```bash
/home/zczd/.local/go-toolchains/go1.26.1/bin/go version
# go version go1.26.1 linux/amd64
```

已执行验证：

```bash
PATH=/home/zczd/.local/go-toolchains/go1.26.1/bin:$PATH gofmt -w audit/sender.go controller/relay.go model/log.go
git diff --check
PATH=/home/zczd/.local/go-toolchains/go1.26.1/bin:$PATH go test ./audit ./model ./controller -run '^$'
```

结果：

- `audit` 编译通过。
- `model` 编译通过。
- `controller` 编译通过。
- 执行完整 `go test ./audit ./model ./controller` 时，`controller` 现有测试在 SQLite 初始化场景失败；失败点不属于审计 hook 编译错误。

GitHub 推送状态：

- 已配置仓库专用 SSH Deploy Key。
- 已推送 `audit-hook` 分支到 `github.com:Bigduang/new-api-audit.git`。
- 远端分支 commit：`8ef856cf656733cfbe6a623394dedc3ccfdc4b0d`
- PR 地址：`https://github.com/Bigduang/new-api-audit/pull/new/audit-hook`

## 报表和任务

执行分类：

```bash
python -m token_audit.cli classify --start 2026-06-02 --end 2026-06-02
```

生成用户/token 用量报表：

```bash
python -m token_audit.cli report --start 2026-06-02 --end 2026-06-02
```

生成疑似非工作复核清单：

```bash
python -m token_audit.cli suspicious --start 2026-06-02 --end 2026-06-02
```

HTTP 接口：

```bash
curl 'http://127.0.0.1:8000/reports/token-usage?start=2026-06-02&end=2026-06-02'
curl 'http://127.0.0.1:8000/reports/suspicious?start=2026-06-02&end=2026-06-02'
curl -X POST 'http://127.0.0.1:8000/jobs/classify?start=2026-06-02&end=2026-06-02'
```

日报建议在次日早晨 06:00 左右执行，此时前一天的请求通常已经完成结算。报表直接读取 `token-audit` 的 SQLite 数据，不再依赖 New-API 主库回填。

## 当前限制和风险

- v1 只审计请求 prompt，不审计模型响应内容。
- 规则分类器可以覆盖常见开发场景，但“工作聊天”和“非工作聊天”边界仍需要 DeepSeek LLM 分类和人工复核。
- prompt 原文已设计为加密保存，但生产环境必须妥善管理 `AUDIT_PROMPT_ENCRYPTION_KEY`。
- New-API Go patch 需要进入你们自己的 New-API fork/镜像构建流程。
- 审计 sender 是非阻塞队列；如果审计服务宕机或队列满，会丢弃审计事件。生产应通过容器健康检查和次日报表中的 `pending_usage` 数量发现异常。

## 下一步建议

1. 保持 New-API 审计 hook 小范围维护，升级 New-API 时优先复用 `audit` sender 周边改动。
2. 每天 06:00 左右审计前一天 SQLite 数据，确认分类结果和 `pending_usage` 数量。
3. 观察疑似非工作/不确定请求样本，逐步补充规则分类器。
4. 接入企业微信日报。
