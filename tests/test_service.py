import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

from token_audit.config import settings_for_tests
from token_audit.crypto import EncryptedText, decrypt_text, sign_payload
from token_audit.db import create_session_factory, migrate
from token_audit.main import create_app
from token_audit.models import AuditClassification, AuditDailyReport, AuditDeadletter, AuditRequest, AuditUserWorkSummary
from token_audit.repository import upsert_usage_event
from token_audit.schemas import UsageAuditEvent


def _client():
    settings = settings_for_tests()
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, app, settings


def _post_signed(client: TestClient, path: str, payload: dict, secret: str = "test-secret"):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    ts = str(int(time.time()))
    return client.post(
        path,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Audit-Timestamp": ts,
            "X-Audit-Signature": sign_payload(secret, ts, raw),
        },
    )


def test_request_and_usage_events_merge_and_encrypt_prompt():
    for client, app, settings in _client():
        usage = {
            "request_id": "req-1",
            "user_id": 7,
            "username": "alice",
            "token_id": 11,
            "token_name": "coding",
            "model_name": "gpt-test",
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "quota": 150,
            "channel_id": 3,
            "group": "default",
            "use_time_seconds": 2,
            "is_stream": False,
            "upstream_request_id": "up-1",
        }
        assert _post_signed(client, "/internal/new-api/audit/usage", usage).status_code == 200

        request = {
            "request_id": "req-1",
            "user_id": 7,
            "username": "alice",
            "token_id": 11,
            "token_name": "coding",
            "model_name": "gpt-test",
            "request_path": "/v1/chat/completions",
            "relay_format": "openai",
            "is_stream": False,
            "prompt_text": "请帮我实现一个 Python FastAPI 接口，并写单元测试",
        }
        assert _post_signed(client, "/internal/new-api/audit/request", request).status_code == 200

        with app.state.session_factory() as session:
            row = session.scalar(select(AuditRequest).where(AuditRequest.request_id == "req-1"))
            assert row is not None
            assert row.relation_status == "complete"
            assert row.prompt_collected is True
            assert row.usage_collected is True
            assert row.prompt_preview == "请帮我实现一个 Python FastAPI 接口，并写单元测试"
            assert row.prompt_ciphertext != request["prompt_text"].encode()
            plaintext = decrypt_text(
                EncryptedText(row.prompt_nonce, row.prompt_ciphertext),
                settings.prompt_encryption_key,
                b"req-1",
            )
            assert plaintext == request["prompt_text"]


def test_daily_report_requires_token_and_renders_html():
    for client, _app, _settings in _client():
        request = {
            "request_id": "req-html-report",
            "user_id": 7,
            "username": "alice",
            "token_id": 11,
            "token_name": "coding",
            "model_name": "gpt-test",
            "request_path": "/v1/chat/completions",
            "relay_format": "openai",
            "prompt_text": "请帮我实现一个 Python FastAPI 接口",
        }
        assert _post_signed(client, "/internal/new-api/audit/request", request).status_code == 200
        usage = {
            "request_id": "req-html-report",
            "user_id": 7,
            "username": "alice",
            "token_id": 11,
            "token_name": "coding",
            "model_name": "gpt-test",
            "prompt_tokens": 2_500_000,
            "completion_tokens": 120_000,
            "quota": 2_620_000,
        }
        assert _post_signed(client, "/internal/new-api/audit/usage", usage).status_code == 200

        unauthorized = client.get("/reports/daily")
        assert unauthorized.status_code == 403

        ok = client.get("/reports/daily?token=test-report-token")
        assert ok.status_code == 200
        assert "text/html" in ok.headers["content-type"]
        assert "Token 审计日报" in ok.text
        assert "alice" in ok.text
        assert 'data-label="总 Tokens"' in ok.text
        assert 'data-label="工作"' in ok.text
        assert "非工作" in ok.text
        assert "不确定" in ok.text
        assert "Non-work" not in ok.text
        assert "2.6M" in ok.text


def test_push_wecom_saves_daily_report_snapshot(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(*args, **kwargs):
        return FakeResponse({"errcode": 0, "access_token": "token"})

    def fake_post(*args, **kwargs):
        return FakeResponse({"errcode": 0, "errmsg": "ok", "msgid": "msg-daily"})

    monkeypatch.setattr("token_audit.wecom.requests.get", fake_get)
    monkeypatch.setattr("token_audit.wecom.requests.post", fake_post)
    settings = settings_for_tests()
    settings = settings.__class__(
        **{
            **settings.__dict__,
            "wecom_corpid": "corp",
            "wecom_appsecret": "secret",
            "wecom_agent_id": 1000010,
            "public_base_url": "https://audit.example.test",
        }
    )
    app = create_app(settings)
    with TestClient(app) as client:
        request = {
            "request_id": "req-daily-snapshot",
            "user_id": 7,
            "username": "alice",
            "token_id": 11,
            "token_name": "coding",
            "model_name": "gpt-test",
            "request_path": "/v1/chat/completions",
            "relay_format": "openai",
            "prompt_text": "请帮我实现一个 Python FastAPI 接口",
        }
        usage = {
            "request_id": "req-daily-snapshot",
            "user_id": 7,
            "username": "alice",
            "token_id": 11,
            "token_name": "coding",
            "model_name": "gpt-test",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "quota": 150,
        }
        assert _post_signed(client, "/internal/new-api/audit/request", request).status_code == 200
        assert _post_signed(client, "/internal/new-api/audit/usage", usage).status_code == 200

        response = client.post("/reports/push-wecom?start=2020-01-01&end=2099-01-01")
        assert response.status_code == 200
        assert response.json()["wecom"]["msgid"] == "msg-daily"

        with app.state.session_factory() as session:
            row = session.scalar(select(AuditDailyReport))
            assert row is not None
            assert row.wecom_msgid == "msg-daily"
            assert "Token 审计日报" in row.title
            assert "工作" in row.wecom_description
            assert "非工作" in row.html_snapshot
            assert "Non-work" not in row.html_snapshot
            summary = json.loads(row.summary_json)
            assert summary["request_count"] == 1
            assert summary["verdict_labels"]["work"] == "工作"


def test_summarize_work_job_writes_user_summary_and_daily_html(monkeypatch):
    posts = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "user": "alice",
                                    "confidence_overall": "high",
                                    "main_functions": [
                                        {
                                            "feature": "订单支付接口",
                                            "activity_type": "后端开发",
                                            "summary": "实现订单支付 API，并补充接口测试思路。",
                                            "evidence": ["实现订单支付接口", "写单元测试"],
                                            "confidence": "high",
                                        }
                                    ],
                                    "other_work": [],
                                    "weak_evidence_notes": "",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    def fake_post(*args, **kwargs):
        posts.append(kwargs["json"])
        return FakeResponse()

    monkeypatch.setattr("token_audit.work_summary.requests.post", fake_post)
    settings = settings_for_tests()
    settings = settings.__class__(
        **{
            **settings.__dict__,
            "llm_enabled": True,
            "llm_base_url": "https://llm.example.test",
            "llm_api_key": "test-key",
            "llm_model": "deepseek-v4-flash",
        }
    )
    app = create_app(settings)
    with TestClient(app) as client:
        request = {
            "request_id": "req-work-summary",
            "user_id": 7,
            "username": "alice",
            "token_id": 11,
            "token_name": "coding",
            "model_name": "gpt-test",
            "request_path": "/v1/chat/completions",
            "relay_format": "openai",
            "prompt_text": "请实现订单支付接口，并写单元测试，注意错误码处理",
        }
        usage = {
            "request_id": "req-work-summary",
            "user_id": 7,
            "username": "alice",
            "token_id": 11,
            "token_name": "coding",
            "model_name": "gpt-test",
            "prompt_tokens": 800,
            "completion_tokens": 200,
            "quota": 1000,
        }
        assert _post_signed(client, "/internal/new-api/audit/request", request).status_code == 200
        assert _post_signed(client, "/internal/new-api/audit/usage", usage).status_code == 200

        summarized = client.post("/jobs/summarize-work?start=2020-01-01&end=2099-01-01").json()
        assert summarized["summarized_users"] == 1
        assert posts[0]["model"] == "deepseek-v4-flash"
        assert posts[0]["response_format"] == {"type": "json_object"}

        ok = client.get("/reports/daily?start=2020-01-01&end=2099-01-01&token=test-report-token")
        assert ok.status_code == 200
        assert "每人工作成果摘要" in ok.text
        assert "订单支付接口" in ok.text
        assert "后端开发" in ok.text
        assert "实现订单支付 API" in ok.text

        with app.state.session_factory() as session:
            summary = session.scalar(select(AuditUserWorkSummary).where(AuditUserWorkSummary.username == "alice"))
            assert summary is not None
            assert summary.request_count == 1
            assert summary.total_tokens == 1000


def test_file_sqlite_persists_request_and_usage_events(tmp_path):
    db_path = tmp_path / "nested" / "token_audit.db"
    settings = settings_for_tests(database_url=f"sqlite:///{db_path}")
    app = create_app(settings)
    with TestClient(app) as client:
        request = {
            "request_id": "req-file-db",
            "user_id": 17,
            "username": "dave",
            "token_id": 23,
            "token_name": "dev",
            "model_name": "gpt-test",
            "request_path": "/v1/chat/completions",
            "relay_format": "openai",
            "prompt_text": "帮我检查这个接口为什么返回 500",
        }
        usage = {
            "request_id": "req-file-db",
            "user_id": 17,
            "username": "dave",
            "token_id": 23,
            "token_name": "dev",
            "model_name": "gpt-test",
            "prompt_tokens": 64,
            "completion_tokens": 32,
            "quota": 96,
        }
        assert _post_signed(client, "/internal/new-api/audit/request", request).status_code == 200
        assert _post_signed(client, "/internal/new-api/audit/usage", usage).status_code == 200

    assert db_path.exists()
    app2 = create_app(settings)
    with TestClient(app2):
        with app2.state.session_factory() as session:
            row = session.scalar(select(AuditRequest).where(AuditRequest.request_id == "req-file-db"))
            assert row is not None
            assert row.relation_status == "complete"
            assert row.username == "dave"
            assert row.prompt_tokens == 64
            assert row.completion_tokens == 32


def test_oversized_payload_is_rejected_without_creating_request():
    settings = settings_for_tests(max_body_bytes=80)
    app = create_app(settings)
    with TestClient(app) as client:
        response = _post_signed(
            client,
            "/internal/new-api/audit/request",
            {"request_id": "too-large", "prompt_text": "x" * 200},
        )
        assert response.status_code == 413

        with app.state.session_factory() as session:
            assert session.scalar(select(AuditRequest).where(AuditRequest.request_id == "too-large")) is None
            deadletter = session.scalar(select(AuditDeadletter).where(AuditDeadletter.event_type == "request"))
            assert deadletter is not None
            assert "payload too large" in deadletter.reason
            assert deadletter.payload == "<payload omitted>"


def test_prompt_omitted_request_preserves_metadata_without_ciphertext():
    for client, app, _settings in _client():
        request = {
            "request_id": "req-omitted",
            "user_id": 7,
            "username": "alice",
            "token_id": 11,
            "token_name": "coding",
            "model_name": "gpt-test",
            "request_path": "/v1/responses",
            "relay_format": "openai_responses",
            "is_stream": True,
            "prompt_hash": "a" * 64,
            "prompt_preview": "超大 prompt 的前 500 字预览",
            "prompt_len": 123456,
            "prompt_omitted": True,
            "prompt_text": "",
        }
        usage = {
            "request_id": "req-omitted",
            "user_id": 7,
            "username": "alice",
            "token_id": 11,
            "token_name": "coding",
            "model_name": "gpt-test",
            "prompt_tokens": 100000,
            "completion_tokens": 20,
            "quota": 100020,
        }
        assert _post_signed(client, "/internal/new-api/audit/request", request).status_code == 200
        assert _post_signed(client, "/internal/new-api/audit/usage", usage).status_code == 200

        with app.state.session_factory() as session:
            row = session.scalar(select(AuditRequest).where(AuditRequest.request_id == "req-omitted"))
            assert row is not None
            assert row.relation_status == "complete"
            assert row.prompt_collected is True
            assert row.prompt_omitted is True
            assert row.prompt_len == 123456
            assert row.prompt_hash == "a" * 64
            assert row.prompt_preview == "超大 prompt 的前 500 字预览"
            assert row.prompt_nonce is None
            assert row.prompt_ciphertext is None
            assert row.username == "alice"
            assert row.token_name == "coding"
            assert row.prompt_tokens == 100000


def test_migrate_adds_prompt_omitted_to_existing_sqlite_schema(tmp_path):
    db_path = tmp_path / "old-schema.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE audit_requests (id INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE)"))

    session_factory = create_session_factory(f"sqlite:///{db_path}")
    migrate(session_factory)

    with engine.connect() as conn:
        columns = {row["name"] for row in conn.execute(text("PRAGMA table_info(audit_requests)")).mappings()}
    assert "prompt_omitted" in columns


def test_concurrent_usage_upsert_with_same_request_id_is_idempotent(tmp_path):
    db_path = tmp_path / "concurrent.db"
    session_factory = create_session_factory(f"sqlite:///{db_path}")
    migrate(session_factory)

    def write_usage(_: int):
        event = UsageAuditEvent(
            request_id="req-concurrent",
            user_id=31,
            username="eve",
            token_id=41,
            token_name="dev",
            model_name="gpt-test",
            prompt_tokens=7,
            completion_tokens=5,
            quota=12,
        )
        with session_factory() as session:
            upsert_usage_event(session, event)
            session.commit()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write_usage, range(16)))

    with session_factory() as session:
        rows = session.scalars(select(AuditRequest).where(AuditRequest.request_id == "req-concurrent")).all()
        assert len(rows) == 1
        assert rows[0].usage_collected is True
        assert rows[0].prompt_tokens == 7
        assert rows[0].completion_tokens == 5


def test_invalid_signature_is_rejected_and_deadlettered():
    for client, app, _settings in _client():
        resp = client.post(
            "/internal/new-api/audit/request",
            json={"request_id": "bad"},
            headers={"X-Audit-Timestamp": str(int(time.time())), "X-Audit-Signature": "bad"},
        )
        assert resp.status_code == 401
        with app.state.session_factory() as session:
            assert session.scalar(select(AuditDeadletter)) is not None


def test_classify_and_reports_identify_non_work_user():
    for client, app, _settings in _client():
        request = {
            "request_id": "req-chat",
            "user_id": 8,
            "username": "bob",
            "token_id": 12,
            "token_name": "general",
            "model_name": "gpt-test",
            "request_path": "/v1/chat/completions",
            "relay_format": "openai",
            "prompt_text": "周末出去玩推荐一下好吃的餐厅，顺便讲个笑话",
        }
        usage = {
            "request_id": "req-chat",
            "user_id": 8,
            "username": "bob",
            "token_id": 12,
            "token_name": "general",
            "model_name": "gpt-test",
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "quota": 30,
        }
        assert _post_signed(client, "/internal/new-api/audit/request", request).status_code == 200
        assert _post_signed(client, "/internal/new-api/audit/usage", usage).status_code == 200
        assert client.post("/jobs/classify?start=2020-01-01&end=2099-01-01").json()["classified"] == 1

        suspicious = client.get("/reports/suspicious?start=2020-01-01&end=2099-01-01").text
        assert "bob" in suspicious
        assert "general" in suspicious
        assert "非工作" in suspicious

        usage_report = client.get("/reports/token-usage?start=2020-01-01&end=2099-01-01").text
        assert "bob" in usage_report
        assert "30" in usage_report
        with app.state.session_factory() as session:
            cls = session.scalar(select(AuditClassification).where(AuditClassification.request_id == "req-chat"))
            assert cls is not None
            assert cls.work_verdict == "non_work"


def test_cleanup_removes_rows_older_than_retention():
    for client, app, _settings in _client():
        old_request = {
            "request_id": "old-req",
            "prompt_text": "历史 prompt",
        }
        new_request = {
            "request_id": "new-req",
            "prompt_text": "新 prompt",
        }
        assert _post_signed(client, "/internal/new-api/audit/request", old_request).status_code == 200
        assert _post_signed(client, "/internal/new-api/audit/request", new_request).status_code == 200
        assert client.post("/jobs/classify?start=2020-01-01&end=2099-01-01").json()["classified"] == 2

        old_at = datetime.now(timezone.utc) - timedelta(days=31)
        with app.state.session_factory() as session:
            row = session.scalar(select(AuditRequest).where(AuditRequest.request_id == "old-req"))
            assert row is not None
            row.created_at = old_at
            session.add(AuditDeadletter(event_type="request", request_id="old-req", reason="old", payload="{}", created_at=old_at))
            session.add(
                AuditUserWorkSummary(
                    period_start=old_at - timedelta(hours=1),
                    period_end=old_at,
                    username="alice",
                    request_count=1,
                    total_tokens=100,
                    summary_json="{}",
                )
            )
            session.add(
                AuditDailyReport(
                    report_date="2020-01-01",
                    period_start=old_at - timedelta(hours=1),
                    period_end=old_at,
                    title="old report",
                    summary_json="{}",
                    html_snapshot="<html></html>",
                )
            )
            session.commit()

        resp = client.post("/jobs/cleanup")
        assert resp.status_code == 200
        assert resp.json()["deleted"]["requests"] == 1
        assert resp.json()["deleted"]["classifications"] == 1
        assert resp.json()["deleted"]["deadletters"] == 1
        assert resp.json()["deleted"]["work_summaries"] == 1
        assert resp.json()["deleted"]["daily_reports"] == 1

        with app.state.session_factory() as session:
            assert session.scalar(select(AuditRequest).where(AuditRequest.request_id == "old-req")) is None
            assert session.scalar(select(AuditClassification).where(AuditClassification.request_id == "old-req")) is None
            assert session.scalar(select(AuditRequest).where(AuditRequest.request_id == "new-req")) is not None
            assert session.scalar(select(AuditUserWorkSummary).where(AuditUserWorkSummary.username == "alice")) is None
            assert session.scalar(select(AuditDailyReport).where(AuditDailyReport.report_date == "2020-01-01")) is None
