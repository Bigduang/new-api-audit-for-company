# new-api 审计补丁

> **归属说明（2026-09-30）**：审计钩子的**源码真相在另一个仓库** ——
> [`Bigduang/new-api-audit`](https://github.com/Bigduang/new-api-audit)（上游 `QuantumNous/new-api` 的 fork）。
> 该 fork 已同步到上游 **v1.0.0-rc.40**，对应 tag：`v1.0.0-rc.40-audit.20260930`，构建直接取该 tag 源码即可。
> 本目录保留补丁副本，作用是**记录审计契约（audit hook 的字段与触发点）**，便于审计服务与网关的对齐，不作为构建来源。


本目录保存「审计版 new-api」相对上游的定制补丁（新增 `audit/` 包 + 在 relay/日志链路埋点）。

## 文件

| 文件 | 适用上游版本 | 说明 |
|---|---|---|
| `new-api-audit-hook.patch` | v1.0.0-rc.20 – rc.37 | 原始补丁。`controller/relay.go` 的第二个 hunk 直接改 `needSensitiveCheck \|\| needCountToken` 条件（该写法在 rc.38+ 失效）。 |
| `new-api-audit-hook.rc40.patch` | v1.0.0-rc.38 – rc.40 | 适配版。上游在 rc.38 把 token 统计抽为 `service.CountRequestToken(...)` 并引入 `relaykit/`，原变量已不存在；本版把审计触发点改为在 `relaycommon.GenRelayInfo()` 之后按需构建 meta。 |

两者均只涉及 4 个文件：

- `audit/sender.go`（新增，审计事件发送器：请求事件 + 用量事件，HMAC 签名，队列异步发送）
- `audit/sender_test.go`（新增）
- `controller/relay.go`（在 `Relay()` 内触发请求事件）
- `model/log.go`（在 `RecordConsumeLog()` 内触发用量事件）

## 适配要点（rc.38+）

```go
// 插在 relaycommon.GenRelayInfo(...) 之后
if audit.EnabledForToken(c.GetString("token_name")) {
    if meta := request.GetTokenCountMeta(); meta != nil {
        promptText := meta.CombineText
        audit.EnqueueRequest(audit.RequestEvent{ /* 字段同原补丁 */ })
    }
}
```

语义等价：仅在 token 启用审计时才构建 `CombineText`（避免为定价走重路径）。

## 构建（可复现）

```bash
# 1) 取上游源码
curl -sL -o rc40.tgz https://codeload.github.com/QuantumNous/new-api/tar.gz/refs/tags/v1.0.0-rc.40
mkdir src && tar xzf rc40.tgz -C src --strip-components=1 && cd src
# 2) 打补丁
patch -p1 --dry-run < ../new-api-audit-hook.rc40.patch   # 应先验证零失败/零 fuzz
patch -p1 < ../new-api-audit-hook.rc40.patch
# 3) 写入版本串（上游 tarball 的 VERSION 为空，不写则二进制版本显示为空）
echo "v1.0.0-rc.40-audit.$(date +%Y%m%d)" > VERSION
# 4) 构建（需 Docker；前端用 bun、后端 Go，均从 Dockerfile 拉基础镜像）
docker build -t new-api-audit:$(date +%Y%m%d)-rc40 .
```

## 环境变量（运行时）

`AUDIT_ENABLED` / `AUDIT_ENDPOINT`（token-audit 地址）/ `AUDIT_SECRET` /
`AUDIT_TIMEOUT_MS` / `AUDIT_QUEUE_SIZE` / `AUDIT_MAX_EVENT_BYTES` /
`AUDIT_EXCLUDED_TOKEN_NAMES`（事件投递目标：`POST {AUDIT_ENDPOINT}/internal/new-api/audit/{request,usage}`，HMAC 签名）。

## 验证记录（2026-09-30，rc.40 上线）

- 上游 rc.30 → rc.40 升级；构建串 `v1.0.0-rc.40-audit.20260930`。
- 切换前用数据库副本 + 独立端口容器做预检：自动迁移 36→37 表；审计链路 E2E 捕获到 `request`（含 `prompt_hash`/`prompt_preview`）与 `usage` 两类事件。
- 切换后经公网真实请求 HTTP 200，审计库 `audit_requests` 出现对应记录（request+usage 双事件齐备）。
- 注意：用不存在的模型名测试会**在 distributor 中间件层就被拒绝**，`controller.Relay` 不执行 → 审计钩子不会触发，需用真实模型验证。
