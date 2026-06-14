import json
import hmac
from contextlib import asynccontextmanager
from urllib.parse import urlencode
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from .config import Settings
from .crypto import verify_signature
from .db import create_session_factory, migrate
from .jobs import classify_requests, cleanup_old_audit_data
from .repository import update_review, upsert_request_event, upsert_usage_event, write_deadletter
from .reports import (
    daily_html_report,
    save_daily_report_snapshot,
    suspicious_report,
    token_usage_report,
    update_daily_report_wecom_result,
    wecom_daily_summary,
)
from .schemas import RequestAuditEvent, ReviewPatch, UsageAuditEvent
from .timeutil import fmt_local, parse_range
from .wecom import push_wecom_text, push_wecom_textcard
from .work_summary import summarize_user_work


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate_runtime()
    session_factory = create_session_factory(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        migrate(session_factory)
        yield

    app = FastAPI(title="Token Audit Service", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = session_factory

    def get_session() -> Session:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/internal/new-api/audit/request")
    async def receive_request_event(
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        x_audit_timestamp: Annotated[str | None, Header()] = None,
        x_audit_signature: Annotated[str | None, Header()] = None,
    ) -> dict:
        payload = await _verified_json(request, session, settings, x_audit_timestamp, x_audit_signature, "request")
        try:
            event = RequestAuditEvent.model_validate(payload)
        except ValidationError as exc:
            write_deadletter(session, "request", f"payload validation failed: {exc}", json.dumps(payload, ensure_ascii=False))
            session.commit()
            raise HTTPException(status_code=422, detail="invalid request audit payload") from exc
        upsert_request_event(session, event, settings.prompt_encryption_key)
        return {"ok": True, "request_id": event.request_id}

    @app.post("/internal/new-api/audit/usage")
    async def receive_usage_event(
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        x_audit_timestamp: Annotated[str | None, Header()] = None,
        x_audit_signature: Annotated[str | None, Header()] = None,
    ) -> dict:
        payload = await _verified_json(request, session, settings, x_audit_timestamp, x_audit_signature, "usage")
        try:
            event = UsageAuditEvent.model_validate(payload)
        except ValidationError as exc:
            write_deadletter(session, "usage", f"payload validation failed: {exc}", json.dumps(payload, ensure_ascii=False))
            session.commit()
            raise HTTPException(status_code=422, detail="invalid usage audit payload") from exc
        upsert_usage_event(session, event)
        return {"ok": True, "request_id": event.request_id}

    @app.post("/jobs/classify")
    def classify_job(
        session: Annotated[Session, Depends(get_session)],
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        force: bool = Query(default=False),
    ) -> dict:
        start_dt, end_dt = parse_range(start, end, settings.timezone)
        count = classify_requests(session, settings, start_dt, end_dt, force=force)
        return {"ok": True, "classified": count}

    @app.post("/jobs/cleanup")
    def cleanup_job(session: Annotated[Session, Depends(get_session)]) -> dict:
        result = cleanup_old_audit_data(session, settings)
        return {"ok": True, "retention_days": settings.retention_days, "deleted": result}

    @app.post("/jobs/summarize-work")
    def summarize_work_job(
        session: Annotated[Session, Depends(get_session)],
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        force: bool = Query(default=False),
    ) -> dict:
        start_dt, end_dt = parse_range(start, end, settings.timezone)
        count = summarize_user_work(session, settings, start_dt, end_dt, force=force)
        return {"ok": True, "summarized_users": count}

    @app.get("/reports/token-usage", response_class=PlainTextResponse)
    def get_token_usage_report(
        session: Annotated[Session, Depends(get_session)],
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
    ) -> str:
        start_dt, end_dt = parse_range(start, end, settings.timezone)
        return token_usage_report(session, start_dt, end_dt, settings.timezone)

    @app.get("/reports/suspicious", response_class=PlainTextResponse)
    def get_suspicious_report(
        session: Annotated[Session, Depends(get_session)],
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
    ) -> str:
        start_dt, end_dt = parse_range(start, end, settings.timezone)
        return suspicious_report(session, start_dt, end_dt, settings.timezone)

    @app.get("/reports/daily", response_class=HTMLResponse)
    def get_daily_report(
        session: Annotated[Session, Depends(get_session)],
        date: str | None = Query(default=None),
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        token: str | None = Query(default=None),
        authorization: Annotated[str | None, Header()] = None,
    ) -> str:
        _authorize_report_access(settings, token, authorization)
        if date:
            start = date
            end = date
        start_dt, end_dt = parse_range(start, end, settings.timezone)
        return daily_html_report(session, start_dt, end_dt, settings.timezone)

    @app.post("/reports/push-wecom")
    def push_report_to_wecom(
        session: Annotated[Session, Depends(get_session)],
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        include_suspicious: bool = Query(default=True),
    ) -> dict:
        start_dt, end_dt = parse_range(start, end, settings.timezone)
        content = token_usage_report(session, start_dt, end_dt, settings.timezone)
        if include_suspicious:
            content = content + "\n\n" + suspicious_report(session, start_dt, end_dt, settings.timezone)
        detail_url = _daily_report_url(settings, start_dt, end_dt)
        daily_report = save_daily_report_snapshot(session, start_dt, end_dt, settings.timezone, detail_url)
        session.commit()
        if detail_url:
            title, description = wecom_daily_summary(session, start_dt, end_dt, settings.timezone, detail_url)
            result = push_wecom_textcard(settings, title, description, detail_url)
        else:
            result = push_wecom_text(settings, content)
        update_daily_report_wecom_result(session, daily_report, result)
        return {"ok": True, "wecom": result}

    @app.patch("/audit-requests/{request_id}/review")
    def review_request(
        request_id: str,
        patch: ReviewPatch,
        session: Annotated[Session, Depends(get_session)],
    ) -> dict:
        row = update_review(session, request_id, patch.review_status, patch.review_note, patch.reviewed_by)
        return {"ok": True, "request_id": row.request_id, "review_status": row.review_status}

    return app


def _authorize_report_access(settings: Settings, token: str | None, authorization: str | None) -> None:
    expected = settings.report_access_token
    if not expected:
        raise HTTPException(status_code=404, detail="report page is not enabled")
    supplied = token or ""
    if not supplied and authorization:
        prefix = "Bearer "
        if authorization.startswith(prefix):
            supplied = authorization[len(prefix) :]
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="invalid report token")


def _daily_report_url(settings: Settings, start, end) -> str:
    if not settings.public_base_url or not settings.report_access_token:
        return ""
    start_date = fmt_local(start, settings.timezone)[:10]
    end_date = fmt_local(end, settings.timezone)[:10]
    query = {"start": start_date, "end": end_date, "token": settings.report_access_token}
    if start_date == end_date:
        query = {"date": start_date, "token": settings.report_access_token}
    return f"{settings.public_base_url}/reports/daily?{urlencode(query)}"


async def _verified_json(
    request: Request,
    session: Session,
    settings: Settings,
    timestamp: str | None,
    signature: str | None,
    event_type: str,
) -> dict:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.max_body_bytes:
                write_deadletter(session, event_type, f"payload too large: content-length={content_length}", "<payload omitted>")
                session.commit()
                raise HTTPException(status_code=413, detail="payload too large")
        except ValueError:
            pass
    raw_body = await request.body()
    if len(raw_body) > settings.max_body_bytes:
        write_deadletter(session, event_type, f"payload too large: size={len(raw_body)}", "<payload omitted>")
        session.commit()
        raise HTTPException(status_code=413, detail="payload too large")
    ok, reason = verify_signature(
        settings.audit_secret,
        timestamp or "",
        signature or "",
        raw_body,
        settings.signature_tolerance_seconds,
    )
    if not ok:
        write_deadletter(session, event_type, reason, raw_body)
        session.commit()
        raise HTTPException(status_code=401, detail=reason)
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        write_deadletter(session, event_type, f"invalid json: {exc}", raw_body)
        session.commit()
        raise HTTPException(status_code=400, detail="invalid json") from exc
    if not isinstance(payload, dict):
        write_deadletter(session, event_type, "payload must be object", raw_body)
        session.commit()
        raise HTTPException(status_code=400, detail="payload must be object")
    return payload


try:
    app = create_app()
except ValueError as exc:
    config_error = str(exc)

    @asynccontextmanager
    async def missing_config_lifespan(app: FastAPI):
        raise RuntimeError(f"Token audit service is not configured: {config_error}")
        yield

    app = FastAPI(title="Token Audit Service", version="0.1.0", lifespan=missing_config_lifespan)
