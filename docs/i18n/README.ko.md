# Token Audit

New-API용 Token 사용량 및 업무 목적 감사 서비스입니다.

언어: [中文](../../README.md) | [English](README.en.md) | [日本語](README.ja.md) | 한국어

## 목적

`token-audit`는 New-API fork가 전송하는 서명된 감사 이벤트를 수신하고, `request_id`로 request event와 usage event를 합쳐 로컬 SQLite에 저장합니다. 이후 요청이 업무 목적이었는지 분류하고 일일 감사 리포트를 생성합니다.

이 서비스는 VPS에서 운영되는 소규모 사내 중계 환경을 기준으로 설계되었습니다. 실시간 차단보다 사후 추적과 감사에 초점을 둡니다. 감사 서비스가 일시적으로 장애가 나더라도 New-API의 정상 요청은 계속 처리되어야 합니다.

## 감사 항목

- 사용자별, token별 요청 수.
- Prompt Tokens, Completion Tokens, Total Tokens, quota.
- 모델명과 요청 경로.
- 업무, 비업무, 불확실, 미분류 건수.
- 의심 요청의 사용자, token, 모델, 분류 이유, 리뷰 상태, prompt preview.
- 사용자별 업무 내용 요약.
- HTML 일일 리포트 스냅샷과 Enterprise WeChat 전송 결과.

## 데이터 흐름

1. New-API가 요청 파싱 후 request event를 보냅니다.
2. New-API가 소비 로그 정산 후 usage event를 보냅니다.
3. `token-audit`가 `request_id` 기준으로 upsert합니다.
4. 전체 prompt는 AES-GCM으로 암호화 저장되며, 리포트에는 기본적으로 `prompt_preview`만 표시됩니다.
5. 분류와 일일 리포트 작업은 보통 다음 날 아침에 전날 데이터를 대상으로 실행합니다.

## API

New-API 내부 연동 API:

| Method | Path | 설명 |
| --- | --- | --- |
| `POST` | `/internal/new-api/audit/request` | 요청 메타데이터와 prompt 수신 |
| `POST` | `/internal/new-api/audit/usage` | 최종 token/quota 사용량 수신 |

운영 및 리포트 API:

| Method | Path | 설명 |
| --- | --- | --- |
| `GET` | `/health` | 헬스 체크 |
| `POST` | `/jobs/classify` | 지정 기간 요청 분류 |
| `POST` | `/jobs/summarize-work` | 사용자별 업무 내용 요약 |
| `POST` | `/jobs/cleanup` | 보존 기간이 지난 데이터 삭제 |
| `GET` | `/reports/token-usage` | 텍스트 사용량 리포트 |
| `GET` | `/reports/suspicious` | 텍스트 의심 요청 목록 |
| `GET` | `/reports/daily` | token으로 보호되는 HTML 일일 리포트 |
| `POST` | `/reports/push-wecom` | 리포트 저장 및 Enterprise WeChat 전송 |
| `PATCH` | `/audit-requests/{request_id}/review` | 수동 리뷰 결과 기록 |

서명 헤더:

```text
X-Audit-Timestamp: <unix timestamp>
X-Audit-Signature: hex(hmac_sha256(timestamp + "." + raw_body, AUDIT_SECRET))
```

## 설정

```bash
cp .env.example .env
python - <<'PY'
import base64, os
print("base64:" + base64.b64encode(os.urandom(32)).decode())
PY
```

주요 환경 변수:

| 변수 | 설명 |
| --- | --- |
| `AUDIT_DATABASE_URL` | SQLAlchemy URL. 운영 환경은 보통 SQLite 파일을 사용합니다. |
| `AUDIT_SECRET` | New-API와 공유하는 HMAC secret. |
| `AUDIT_PROMPT_ENCRYPTION_KEY` | AES-GCM 키. `base64:`, `hex:`, 일반 문자열을 지원합니다. |
| `AUDIT_TIMEZONE` | 리포트 표시 시간대. 보통 `Asia/Shanghai`. |
| `AUDIT_RETENTION_DAYS` | 데이터 보존 일수. 보통 30일. |
| `AUDIT_MAX_BODY_BYTES` | 수신 payload 최대 크기. |
| `AUDIT_PUBLIC_BASE_URL` | 리포트 링크의 공개 base URL. |
| `AUDIT_REPORT_ACCESS_TOKEN` | `/reports/daily` 접근 token. |

LLM 분류 및 업무 요약:

| 변수 | 설명 |
| --- | --- |
| `AUDIT_LLM_ENABLED` | LLM 기능 활성화. |
| `AUDIT_LLM_BASE_URL` | OpenAI-compatible endpoint. 예: `https://api.deepseek.com`. |
| `AUDIT_LLM_API_KEY` | LLM API key. git에 커밋하지 마세요. |
| `AUDIT_LLM_MODEL` | 예: `deepseek-v4-flash`. |

Enterprise WeChat:

| 변수 | 설명 |
| --- | --- |
| `WX_CORPID` | 기업 ID |
| `WX_APPSECRET` | 앱 secret |
| `WX_AGENT_ID` | 앱 AgentId |

## Docker 배포

New-API와 같은 Docker 네트워크에 붙이면 New-API는 다음 endpoint를 사용할 수 있습니다:

```env
AUDIT_ENDPOINT=http://token-audit:8000
```

시작:

```bash
mkdir -p data
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml up -d
docker logs -f token-audit
```

`deploy/docker-compose.yml`은 외부 네트워크 `proxy_newapi-network`를 기본으로 사용합니다. 환경이 다르면 수정하세요.

## New-API 연동

New-API 권장 설정:

```env
AUDIT_ENABLED=true
AUDIT_ENDPOINT=http://token-audit:8000
AUDIT_SECRET=<same-as-token-audit>
AUDIT_TIMEOUT_MS=800
AUDIT_QUEUE_SIZE=1000
AUDIT_MAX_EVENT_BYTES=1048576
AUDIT_EXCLUDED_TOKEN_NAMES=audit-classifier
```

감사 sender는 비동기 non-blocking 방식입니다. 감사 endpoint 장애, 큐 포화, 큰 이벤트가 발생해도 New-API의 사용자 요청은 계속 처리됩니다.

## 일일 작업

```bash
python -m token_audit.cli classify --start 2026-06-02 --end 2026-06-02
python -m token_audit.cli summarize-work --start 2026-06-02 --end 2026-06-02
python -m token_audit.cli push-wecom --start 2026-06-02 --end 2026-06-02
python -m token_audit.cli cleanup
```

Docker helper:

```bash
/opt/token-audit/deploy/scripts/run-daily-audit.sh 2026-06-02
```

cron 예시:

```cron
05 6 * * * /opt/token-audit/deploy/scripts/run-daily-audit.sh >> /opt/token-audit/data/daily-audit.log 2>&1
```

## 개발

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
pip install -r requirements-dev.txt
pytest -q
```

## 보안

- `.env`, SQLite DB, 로그, 리포트 출력, 실제 API key를 커밋하지 마세요.
- `AUDIT_PROMPT_ENCRYPTION_KEY`는 안전하게 백업해야 합니다.
- 공개 nginx는 일반적으로 `/reports/daily`만 노출하고 `AUDIT_REPORT_ACCESS_TOKEN`으로 보호합니다.
- LLM 업무 요약은 전체 prompt가 아니라 `prompt_preview`를 사용합니다.
- 분류기 자체 token 이름은 `AUDIT_EXCLUDED_TOKEN_NAMES`에 추가해 직원 사용량 통계를 오염시키지 않도록 합니다.
