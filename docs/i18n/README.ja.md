# Token Audit

New-API 向けの Token 使用量および業務利用監査サービスです。

言語： [中文](../../README.md) | [English](README.en.md) | 日本語 | [한국어](README.ko.md)

## 目的

`token-audit` は New-API fork から署名付き監査イベントを受け取り、`request_id` で request event と usage event を結合し、ローカル SQLite に保存します。その後、リクエストが業務目的だったかを分類し、日次レポートを生成します。

このサービスは小規模な社内 VPS 中継環境を想定しています。リアルタイム遮断ではなく、事後監査と追跡性を重視します。監査サービスが一時的に落ちても New-API の通常リクエストを止めない設計です。

## 監査内容

- ユーザー別、token 別のリクエスト数。
- Prompt Tokens、Completion Tokens、Total Tokens、quota。
- モデル名とリクエストパス。
- 業務、非業務、不確定、未分類の件数。
- 疑わしいリクエストのユーザー、token、モデル、分類理由、レビュー状態、prompt preview。
- ユーザーごとの作業内容サマリー。
- HTML 日報スナップショットと Enterprise WeChat 送信結果。

## データフロー

1. New-API がリクエスト解析後に request event を送信します。
2. New-API が消費ログ確定後に usage event を送信します。
3. `token-audit` が `request_id` で upsert します。
4. 完全な prompt は AES-GCM で暗号化保存され、レポートには通常 `prompt_preview` のみ表示されます。
5. 分類と日報生成は、通常翌朝に前日分を対象として実行します。

## API

New-API から呼び出される内部 API：

| Method | Path | 説明 |
| --- | --- | --- |
| `POST` | `/internal/new-api/audit/request` | リクエスト情報と prompt を受信 |
| `POST` | `/internal/new-api/audit/usage` | 最終 token/quota 使用量を受信 |

運用 API：

| Method | Path | 説明 |
| --- | --- | --- |
| `GET` | `/health` | ヘルスチェック |
| `POST` | `/jobs/classify` | 指定期間の分類 |
| `POST` | `/jobs/summarize-work` | ユーザー別の作業内容要約 |
| `POST` | `/jobs/cleanup` | 保持期間を過ぎたデータを削除 |
| `GET` | `/reports/token-usage` | テキスト形式の使用量レポート |
| `GET` | `/reports/suspicious` | テキスト形式の要レビュー一覧 |
| `GET` | `/reports/daily` | token で保護された HTML 日報 |
| `POST` | `/reports/push-wecom` | 日報を保存し Enterprise WeChat へ送信 |
| `PATCH` | `/audit-requests/{request_id}/review` | 人手レビュー結果を記録 |

署名ヘッダー：

```text
X-Audit-Timestamp: <unix timestamp>
X-Audit-Signature: hex(hmac_sha256(timestamp + "." + raw_body, AUDIT_SECRET))
```

## 設定

```bash
cp .env.example .env
python - <<'PY'
import base64, os
print("base64:" + base64.b64encode(os.urandom(32)).decode())
PY
```

主な環境変数：

| 変数 | 説明 |
| --- | --- |
| `AUDIT_DATABASE_URL` | SQLAlchemy URL。本番では SQLite ファイルを推奨。 |
| `AUDIT_SECRET` | New-API と共有する HMAC secret。 |
| `AUDIT_PROMPT_ENCRYPTION_KEY` | AES-GCM 鍵。`base64:`、`hex:`、通常文字列をサポート。 |
| `AUDIT_TIMEZONE` | レポート表示タイムゾーン。通常 `Asia/Shanghai`。 |
| `AUDIT_RETENTION_DAYS` | データ保持日数。通常 30 日。 |
| `AUDIT_MAX_BODY_BYTES` | 受信 payload の最大サイズ。 |
| `AUDIT_PUBLIC_BASE_URL` | 日報リンクの公開 URL。 |
| `AUDIT_REPORT_ACCESS_TOKEN` | `/reports/daily` のアクセス token。 |

LLM 分類と作業要約：

| 変数 | 説明 |
| --- | --- |
| `AUDIT_LLM_ENABLED` | LLM 機能を有効化。 |
| `AUDIT_LLM_BASE_URL` | OpenAI-compatible endpoint。例：`https://api.deepseek.com`。 |
| `AUDIT_LLM_API_KEY` | LLM API key。git にコミットしないこと。 |
| `AUDIT_LLM_MODEL` | 例：`deepseek-v4-flash`。 |

Enterprise WeChat：

| 変数 | 説明 |
| --- | --- |
| `WX_CORPID` | 企業 ID |
| `WX_APPSECRET` | アプリ secret |
| `WX_AGENT_ID` | アプリ AgentId |

## Docker デプロイ

New-API と同じ Docker ネットワークに参加させると、New-API 側では次の endpoint を利用できます：

```env
AUDIT_ENDPOINT=http://token-audit:8000
```

起動：

```bash
mkdir -p data
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml up -d
docker logs -f token-audit
```

`deploy/docker-compose.yml` は外部ネットワーク `proxy_newapi-network` を想定しています。環境に合わせて変更してください。

## New-API 連携

New-API 側の推奨設定：

```env
AUDIT_ENABLED=true
AUDIT_ENDPOINT=http://token-audit:8000
AUDIT_SECRET=<same-as-token-audit>
AUDIT_TIMEOUT_MS=800
AUDIT_QUEUE_SIZE=1000
AUDIT_MAX_EVENT_BYTES=1048576
AUDIT_EXCLUDED_TOKEN_NAMES=audit-classifier
```

監査 sender は非同期・非ブロッキングです。監査 endpoint 障害、キュー満杯、大きすぎるイベントがあっても、New-API の通常リクエストは継続されます。

## 日次ジョブ

```bash
python -m token_audit.cli classify --start 2026-06-02 --end 2026-06-02
python -m token_audit.cli summarize-work --start 2026-06-02 --end 2026-06-02
python -m token_audit.cli push-wecom --start 2026-06-02 --end 2026-06-02
python -m token_audit.cli cleanup
```

Docker helper：

```bash
/opt/token-audit/deploy/scripts/run-daily-audit.sh 2026-06-02
```

cron 例：

```cron
05 6 * * * /opt/token-audit/deploy/scripts/run-daily-audit.sh >> /opt/token-audit/data/daily-audit.log 2>&1
```

## 開発

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
pip install -r requirements-dev.txt
pytest -q
```

## セキュリティ

- `.env`、SQLite DB、ログ、レポート出力、実 API key をコミットしないでください。
- `AUDIT_PROMPT_ENCRYPTION_KEY` は必ず安全にバックアップしてください。
- 公開 nginx は通常 `/reports/daily` のみを公開し、`AUDIT_REPORT_ACCESS_TOKEN` で保護します。
- LLM の作業要約には完全な prompt ではなく `prompt_preview` を使用します。
- 分類器用 token 名は `AUDIT_EXCLUDED_TOKEN_NAMES` に追加してください。
