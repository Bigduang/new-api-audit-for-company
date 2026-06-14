from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import load_only

from .audit_users import (
    AuditUserListItem,
    display_name_for,
    get_audit_user,
    identity_key_for,
    list_audit_users,
    load_audit_user_map,
    request_identity_key,
    request_user_filter,
    sync_audit_users_from_requests,
    update_audit_user,
    user_filter_options,
    user_request_history,
)
from .config import Settings
from .crypto import decrypt_text
from .models import AuditClassification, AuditRequest, AuditUser
from .repository import encrypted_text_from_row
from .reports import VERDICT_LABELS, _compact_number, daily_html_report
from .timeutil import fmt_local, parse_range

COOKIE_NAME = "token_audit_admin"
SESSION_PURPOSE = b"token-audit-admin-session"
ADMIN_DIST_DIR = Path(__file__).parent / "admin_dist"
ADMIN_INDEX_FILE = ADMIN_DIST_DIR / "index.html"
PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 100


def create_admin_router() -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin"])

    @router.get("/api/session", include_in_schema=False)
    def session_info(request: Request) -> dict:
        settings = _settings(request)
        session = _read_session(request, settings)
        return {
            "authenticated": session is not None,
            "admin": session.get("sub") if session else "",
            "csrf_token": session.get("csrf") if session else "",
            "configured": _admin_configured(settings),
            "timezone": settings.timezone,
        }

    @router.post("/api/login", include_in_schema=False)
    async def login(request: Request) -> JSONResponse:
        settings = _settings(request)
        payload = await _read_json(request)
        if not _admin_configured(settings):
            raise HTTPException(status_code=503, detail="admin account is not configured")
        username = str(payload.get("username") or "")
        password = str(payload.get("password") or "")
        if not hmac.compare_digest(username, settings.admin_user) or not hmac.compare_digest(password, settings.admin_password):
            raise HTTPException(status_code=401, detail="invalid admin credentials")
        session_payload = _new_session_payload(settings)
        response = JSONResponse(
            {
                "ok": True,
                "admin": session_payload["sub"],
                "csrf_token": session_payload["csrf"],
            }
        )
        response.set_cookie(
            COOKIE_NAME,
            _sign_session(settings, session_payload),
            max_age=settings.admin_session_ttl_seconds,
            httponly=True,
            samesite="lax",
            path="/admin",
        )
        return response

    @router.post("/api/logout", include_in_schema=False)
    async def logout(request: Request) -> JSONResponse:
        session = _require_admin_api(request)
        await _require_csrf(request, session)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE_NAME, path="/admin")
        return response

    @router.get("/api/dashboard", include_in_schema=False)
    def dashboard(request: Request, date: str | None = Query(default=None)) -> dict:
        _require_admin_api(request)
        settings = _settings(request)
        start_dt, end_dt = parse_range(date, date, settings.timezone) if date else parse_range(None, None, settings.timezone)
        with _session(request) as db:
            return _dashboard_stats(db, start_dt, end_dt, settings.timezone)

    @router.get("/api/users", include_in_schema=False)
    def users(
        request: Request,
        status: str = Query(default="all"),
        configured: str = Query(default="all"),
        q: str = Query(default=""),
    ) -> dict:
        _require_admin_api(request)
        with _session(request) as db:
            rows = list_audit_users(db, status=status, configured=configured, query=q)
            return {
                "ok": True,
                "users": [_audit_user_list_item_payload(row, _settings(request).timezone) for row in rows],
                "filters": {"status": status, "configured": configured, "q": q},
            }

    @router.post("/api/users/sync", include_in_schema=False)
    async def sync_users(request: Request) -> JSONResponse:
        session_payload = _require_admin_api(request)
        await _require_csrf(request, session_payload)
        with _session(request) as db:
            created = sync_audit_users_from_requests(db)
            db.commit()
        return JSONResponse({"ok": True, "created": created})

    @router.get("/api/users/{identity_key}/requests", include_in_schema=False)
    def user_requests(
        request: Request,
        identity_key: str,
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        verdict: str = Query(default=""),
        token: str = Query(default=""),
        model: str = Query(default=""),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=PAGE_SIZE_DEFAULT, ge=1, le=PAGE_SIZE_MAX),
    ) -> dict:
        _require_admin_api(request)
        settings = _settings(request)
        start_dt = end_dt = None
        if start or end:
            start_dt, end_dt = parse_range(start, end, settings.timezone)
        with _session(request) as db:
            user = _get_audit_user_or_404(db, identity_key)
            rows, total = user_request_history(
                db,
                identity_key,
                start=start_dt,
                end=end_dt,
                verdict=verdict,
                token=token,
                model=model,
                page=page,
                page_size=page_size,
            )
            user_map = load_audit_user_map(db)
            return {
                "ok": True,
                "user": _audit_user_payload(user, settings.timezone),
                "requests": [_history_item(req, cls, user_map, settings.timezone) for req, cls in rows],
                "pagination": _pagination(total, page, page_size),
                "options": user_filter_options(db, identity_key) | {"verdicts": _verdict_options()},
                "filters": {"start": start or "", "end": end or "", "verdict": verdict, "token": token, "model": model},
            }

    @router.get("/api/users/{identity_key}", include_in_schema=False)
    def user_detail(request: Request, identity_key: str) -> dict:
        _require_admin_api(request)
        settings = _settings(request)
        with _session(request) as db:
            user = _get_audit_user_or_404(db, identity_key)
            return {
                "ok": True,
                "user": _audit_user_payload(user, settings.timezone),
                "stats": _user_stats(db, identity_key, settings.timezone),
                "options": user_filter_options(db, identity_key) | {"verdicts": _verdict_options()},
            }

    @router.patch("/api/users/{identity_key}", include_in_schema=False)
    async def patch_user(request: Request, identity_key: str) -> JSONResponse:
        session_payload = _require_admin_api(request)
        await _require_csrf(request, session_payload)
        payload = await _read_json(request)
        with _session(request) as db:
            row = update_audit_user(
                db,
                identity_key,
                display_name=payload.get("display_name") if "display_name" in payload else None,
                audit_enabled=payload.get("audit_enabled") if "audit_enabled" in payload else None,
                notes=payload.get("notes") if "notes" in payload else None,
            )
            db.commit()
            return JSONResponse({"ok": True, "user": _audit_user_payload(row, _settings(request).timezone)})

    @router.get("/api/requests", include_in_schema=False)
    def requests(
        request: Request,
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        verdict: str = Query(default=""),
        user: str = Query(default=""),
        token: str = Query(default=""),
        model: str = Query(default=""),
        q: str = Query(default=""),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=PAGE_SIZE_DEFAULT, ge=1, le=PAGE_SIZE_MAX),
    ) -> dict:
        _require_admin_api(request)
        settings = _settings(request)
        start_dt = end_dt = None
        if start or end:
            start_dt, end_dt = parse_range(start, end, settings.timezone)
        with _session(request) as db:
            rows, total = _request_history(
                db,
                start=start_dt,
                end=end_dt,
                verdict=verdict,
                user_identity=user,
                token=token,
                model=model,
                query=q,
                page=page,
                page_size=page_size,
            )
            user_map = load_audit_user_map(db)
            return {
                "ok": True,
                "requests": [_history_item(req, cls, user_map, settings.timezone) for req, cls in rows],
                "pagination": _pagination(total, page, page_size),
                "options": _request_filter_options(db) | {"verdicts": _verdict_options()},
                "filters": {
                    "start": start or "",
                    "end": end or "",
                    "verdict": verdict,
                    "user": user,
                    "token": token,
                    "model": model,
                    "q": q,
                },
            }

    @router.get("/api/requests/{request_id}/preview", include_in_schema=False)
    def request_preview(request: Request, request_id: str) -> dict:
        _require_admin_api(request)
        settings = _settings(request)
        with _session(request) as db:
            row = db.scalar(
                select(AuditRequest)
                .options(
                    load_only(
                        AuditRequest.request_id,
                        AuditRequest.created_at,
                        AuditRequest.user_id,
                        AuditRequest.username,
                        AuditRequest.token_id,
                        AuditRequest.token_name,
                        AuditRequest.model_name,
                        AuditRequest.prompt_preview,
                        AuditRequest.prompt_len,
                        AuditRequest.prompt_omitted,
                        AuditRequest.prompt_nonce,
                        AuditRequest.prompt_ciphertext,
                    )
                )
                .where(AuditRequest.request_id == request_id)
            )
            if row is None:
                raise HTTPException(status_code=404, detail="request not found")
            encrypted = encrypted_text_from_row(row)
            prompt_text = row.prompt_preview or ""
            prompt_source = "preview"
            decrypt_error = ""
            if encrypted is not None:
                try:
                    prompt_text = decrypt_text(encrypted, settings.prompt_encryption_key, row.request_id.encode("utf-8"))
                    prompt_source = "encrypted_full"
                except Exception:
                    decrypt_error = "完整 Prompt 解密失败，当前仅显示预览。"
                    prompt_source = "decrypt_error"
            elif row.prompt_omitted:
                prompt_source = "omitted_preview"

            user_map = load_audit_user_map(db)
            key = identity_key_for(row.user_id, row.username)
            user = user_map.get(key)
            return {
                "ok": True,
                "request_id": row.request_id,
                "time": fmt_local(row.created_at, settings.timezone),
                "identity_key": key,
                "user": display_name_for(user, row.user_id, row.username),
                "token": row.token_name or row.token_id or "unknown",
                "model": row.model_name or "unknown",
                "prompt_preview": prompt_text,
                "prompt_preview_short": row.prompt_preview or "",
                "prompt_text": prompt_text,
                "prompt_source": prompt_source,
                "decrypt_error": decrypt_error,
                "prompt_len": row.prompt_len,
                "prompt_omitted": row.prompt_omitted,
            }

    @router.get("/api/report-url", include_in_schema=False)
    def report_url(request: Request, date: str | None = Query(default=None)) -> dict:
        _require_admin_api(request)
        selected_date = _selected_report_date(_settings(request), date)
        return {
            "ok": True,
            "date": selected_date,
            "url": f"/admin/reports/daily/view?{urlencode({'date': selected_date})}",
        }

    @router.get("/reports/daily/view", response_class=HTMLResponse, include_in_schema=False)
    def admin_daily_report_view(
        request: Request,
        date: str | None = Query(default=None),
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
    ):
        session_payload = _require_admin_page(request)
        if isinstance(session_payload, RedirectResponse):
            return session_payload
        settings = _settings(request)
        if date:
            start = date
            end = date
        start_dt, end_dt = parse_range(start, end, settings.timezone)
        with _session(request) as db:
            return HTMLResponse(daily_html_report(db, start_dt, end_dt, settings.timezone))

    @router.get("", include_in_schema=False)
    def admin_root():
        return _spa_index_response()

    @router.get("/{path:path}", include_in_schema=False)
    def admin_spa(path: str):
        if path.startswith("api/") or path.startswith("assets/"):
            raise HTTPException(status_code=404, detail="not found")
        return _spa_index_response()

    return router


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _session(request: Request):
    return request.app.state.session_factory()


def _get_audit_user_or_404(db, identity_key: str) -> AuditUser:
    try:
        return get_audit_user(db, identity_key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="audit user not found") from exc


def _admin_configured(settings: Settings) -> bool:
    return bool(settings.admin_user and settings.admin_password)


async def _read_json(request: Request) -> dict:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid json") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be object")
    return payload


def _require_admin_page(request: Request) -> dict | RedirectResponse:
    session = _read_session(request, _settings(request))
    if session is None:
        return RedirectResponse("/admin/login", status_code=303)
    return session


def _require_admin_api(request: Request) -> dict:
    session = _read_session(request, _settings(request))
    if session is None:
        raise HTTPException(status_code=401, detail="admin login required")
    return session


async def _require_csrf(request: Request, session: dict) -> None:
    supplied = request.headers.get("X-CSRF-Token", "")
    if not supplied or not hmac.compare_digest(supplied, str(session.get("csrf") or "")):
        raise HTTPException(status_code=403, detail="invalid csrf token")


def _new_session_payload(settings: Settings) -> dict:
    now = int(time.time())
    return {
        "sub": settings.admin_user,
        "iat": now,
        "exp": now + max(settings.admin_session_ttl_seconds, 60),
        "csrf": secrets.token_urlsafe(24),
    }


def _session_key(settings: Settings) -> bytes:
    return hmac.new(settings.audit_secret.encode("utf-8"), SESSION_PURPOSE, hashlib.sha256).digest()


def _sign_session(settings: Settings, payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(_session_key(settings), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _read_session(request: Request, settings: Settings) -> dict | None:
    cookie = request.cookies.get(COOKIE_NAME, "")
    if not cookie or "." not in cookie:
        return None
    encoded, signature = cookie.rsplit(".", 1)
    expected = hmac.new(_session_key(settings), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode((encoded + padding).encode("ascii")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    if payload.get("sub") != settings.admin_user:
        return None
    return payload


def _spa_index_response() -> FileResponse | HTMLResponse:
    if ADMIN_INDEX_FILE.exists():
        return FileResponse(ADMIN_INDEX_FILE, media_type="text/html")
    return HTMLResponse(
        """<!doctype html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Token Audit Admin</title></head>
<body style="margin:0;background:#090b16;color:#f8fbff;font-family:system-ui,sans-serif">
<div id="token-audit-admin-root" style="min-height:100vh;display:grid;place-items:center;padding:24px;text-align:center">
  <div><h1>Token Audit Admin</h1><p>React 管理端尚未构建，请先执行 frontend/admin 的 npm run build。</p></div>
</div>
</body></html>"""
    )


def _selected_report_date(settings: Settings, date: str | None) -> str:
    value = (date or "").strip()
    if value:
        return value[:10]
    return datetime.now(ZoneInfo(settings.timezone)).date().isoformat()


def _dashboard_stats(db, today_start, today_end, tz_name: str) -> dict:
    users = db.scalars(select(AuditUser)).all()
    token_expr = AuditRequest.prompt_tokens + AuditRequest.completion_tokens
    all_request_count, total_tokens = db.execute(select(func.count(AuditRequest.id), func.sum(token_expr))).one()
    today_request_count, today_total_tokens, today_quota = db.execute(
        select(func.count(AuditRequest.id), func.sum(token_expr), func.sum(AuditRequest.quota)).where(
            AuditRequest.created_at >= today_start,
            AuditRequest.created_at <= today_end,
        )
    ).one()
    user_map = load_audit_user_map(db)
    top_users = _top_users_today(db, today_start, today_end, user_map)
    return {
        "ok": True,
        "date": fmt_local(today_start, tz_name)[:10],
        "user_count": len(users),
        "enabled_user_count": sum(1 for user in users if user.audit_enabled),
        "unconfigured_user_count": sum(1 for user in users if not user.display_name.strip()),
        "all_request_count": int(all_request_count or 0),
        "all_total_tokens": int(total_tokens or 0),
        "today_request_count": int(today_request_count or 0),
        "today_total_tokens": int(today_total_tokens or 0),
        "today_quota": int(today_quota or 0),
        "top_users": top_users,
    }


def _top_users_today(db, today_start, today_end, user_map: dict[str, AuditUser]) -> list[dict]:
    token_expr = AuditRequest.prompt_tokens + AuditRequest.completion_tokens
    rows = db.execute(
        select(
            AuditRequest.user_id,
            AuditRequest.username,
            func.count(AuditRequest.id),
            func.sum(AuditRequest.prompt_tokens),
            func.sum(AuditRequest.completion_tokens),
            func.sum(token_expr),
            func.sum(AuditRequest.quota),
        )
        .where(AuditRequest.created_at >= today_start, AuditRequest.created_at <= today_end)
        .group_by(AuditRequest.user_id, AuditRequest.username)
    ).all()
    data: dict[str, dict] = {}
    for user_id, username, request_count, prompt_tokens, completion_tokens, total_tokens, quota in rows:
        key = identity_key_for(user_id, username)
        user = user_map.get(key)
        name = display_name_for(user, user_id, username)
        item = data.setdefault(
            key,
            {
                "identity_key": key,
                "name": name,
                "tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "quota": 0,
                "requests": 0,
                "audit_enabled": bool(user and user.audit_enabled),
                "models": Counter(),
                "tokens_by_name": Counter(),
            },
        )
        item["tokens"] += int(total_tokens or 0)
        item["prompt_tokens"] += int(prompt_tokens or 0)
        item["completion_tokens"] += int(completion_tokens or 0)
        item["quota"] += int(quota or 0)
        item["requests"] += int(request_count or 0)

    model_rows = db.execute(
        select(AuditRequest.user_id, AuditRequest.username, AuditRequest.model_name, func.count(AuditRequest.id))
        .where(AuditRequest.created_at >= today_start, AuditRequest.created_at <= today_end)
        .group_by(AuditRequest.user_id, AuditRequest.username, AuditRequest.model_name)
    ).all()
    for user_id, username, model_name, model_count in model_rows:
        key = identity_key_for(user_id, username)
        if key in data:
            data[key]["models"][model_name or "N/A"] += int(model_count or 0)

    token_rows = db.execute(
        select(AuditRequest.user_id, AuditRequest.username, AuditRequest.token_id, AuditRequest.token_name, func.count(AuditRequest.id))
        .where(AuditRequest.created_at >= today_start, AuditRequest.created_at <= today_end)
        .group_by(AuditRequest.user_id, AuditRequest.username, AuditRequest.token_id, AuditRequest.token_name)
    ).all()
    for user_id, username, token_id, token_name, token_count in token_rows:
        key = identity_key_for(user_id, username)
        if key in data:
            data[key]["tokens_by_name"][token_name or str(token_id or "unknown")] += int(token_count or 0)

    items = sorted(data.values(), key=lambda item: item["tokens"], reverse=True)[:5]
    for item in items:
        models = item.pop("models")
        tokens_by_name = item.pop("tokens_by_name")
        item["top_model"] = models.most_common(1)[0][0] if models else "N/A"
        item["top_token"] = tokens_by_name.most_common(1)[0][0] if tokens_by_name else "unknown"
    return items


def _request_history(
    db,
    *,
    start,
    end,
    verdict: str,
    user_identity: str,
    token: str,
    model: str,
    query: str,
    page: int,
    page_size: int,
) -> tuple[list[tuple[AuditRequest, AuditClassification | None]], int]:
    conditions = []
    if start is not None:
        conditions.append(AuditRequest.created_at >= start)
    if end is not None:
        conditions.append(AuditRequest.created_at <= end)
    if user_identity:
        try:
            conditions.append(request_user_filter(user_identity))
        except ValueError:
            user_id, username = _parse_user_filter(user_identity)
            if user_id is not None:
                conditions.append(AuditRequest.user_id == user_id)
            elif username is not None:
                conditions.append(AuditRequest.username == username)
    if token:
        conditions.append(or_(AuditRequest.token_name == token, AuditRequest.token_id == _safe_int(token)))
    if model:
        conditions.append(AuditRequest.model_name == model)
    if query:
        pattern = f"%{query}%"
        conditions.append(or_(AuditRequest.prompt_preview.like(pattern), AuditRequest.request_id.like(pattern)))
    sql = (
        select(AuditRequest, AuditClassification)
        .outerjoin(AuditClassification, AuditClassification.request_id == AuditRequest.request_id)
        .options(_request_summary_load(), _classification_summary_load())
        .where(*conditions)
    )
    if verdict:
        sql = sql.where(AuditClassification.work_verdict == verdict)
    total = db.scalar(select(func.count()).select_from(sql.subquery())) or 0
    rows = db.execute(sql.order_by(AuditRequest.created_at.desc()).offset(max(page - 1, 0) * page_size).limit(page_size)).all()
    return rows, int(total)


def _request_filter_options(db) -> dict[str, list]:
    rows = db.execute(
        select(
            AuditRequest.user_id,
            AuditRequest.username,
            AuditRequest.token_id,
            AuditRequest.token_name,
            AuditRequest.model_name,
        )
        .order_by(AuditRequest.created_at.desc())
        .limit(2000)
    ).all()
    user_map = load_audit_user_map(db)
    users = {}
    tokens = set()
    models = set()
    for user_id, username, token_id, token_name, model_name in rows:
        key = identity_key_for(user_id, username)
        user = user_map.get(key)
        users[key] = display_name_for(user, user_id, username)
        if token_name or token_id:
            tokens.add(token_name or str(token_id or ""))
        if model_name:
            models.add(model_name)
    return {
        "users": [{"identity_key": key, "name": name} for key, name in sorted(users.items(), key=lambda item: item[1])],
        "tokens": sorted(tokens),
        "models": sorted(models),
    }


def _history_item(req: AuditRequest, cls: AuditClassification | None, user_map: dict[str, AuditUser], tz_name: str) -> dict:
    user = user_map.get(request_identity_key(req))
    verdict = cls.work_verdict if cls else "unclassified"
    preview = _clean_preview(req.prompt_preview)
    preview_short = _short_preview(preview)
    return {
        "request_id": req.request_id,
        "identity_key": identity_key_for(req.user_id, req.username),
        "time": fmt_local(req.created_at, tz_name),
        "user": display_name_for(user, req.user_id, req.username),
        "audit_enabled": bool(user and user.audit_enabled),
        "token": req.token_name or req.token_id or "unknown",
        "model": req.model_name or "unknown",
        "tokens": req.prompt_tokens + req.completion_tokens,
        "tokens_compact": _compact_number(req.prompt_tokens + req.completion_tokens),
        "quota": req.quota,
        "quota_compact": _compact_number(req.quota),
        "verdict": verdict,
        "verdict_label": VERDICT_LABELS.get(verdict, "未分类"),
        "category": cls.category if cls else "未分类",
        "confidence": cls.confidence if cls else 0,
        "review_status": cls.review_status if cls else "pending",
        "reason": cls.reason if cls else "",
        "preview": preview_short,
        "preview_short": preview_short,
    }


def _audit_user_payload(user: AuditUser, tz_name: str) -> dict:
    return {
        "identity_key": user.identity_key,
        "user_id": user.user_id,
        "username": user.username,
        "display_name": user.display_name,
        "display_label": display_name_for(user, user.user_id, user.username),
        "audit_enabled": user.audit_enabled,
        "notes": user.notes,
        "first_seen_at": fmt_local(user.first_seen_at, tz_name),
        "last_seen_at": fmt_local(user.last_seen_at, tz_name),
        "created_at": fmt_local(user.created_at, tz_name),
        "updated_at": fmt_local(user.updated_at, tz_name),
    }


def _audit_user_list_item_payload(item: AuditUserListItem, tz_name: str) -> dict:
    payload = asdict(item)
    payload["first_seen_at"] = fmt_local(item.first_seen_at, tz_name)
    payload["last_seen_at"] = fmt_local(item.last_seen_at, tz_name)
    payload["display_label"] = item.display_name or item.username or (str(item.user_id) if item.user_id is not None else "unknown")
    payload["total_tokens_compact"] = _compact_number(item.total_tokens)
    return payload


def _user_stats(db, identity_key: str, tz_name: str) -> dict:
    rows = db.execute(
        select(
            AuditRequest.created_at,
            AuditRequest.prompt_tokens,
            AuditRequest.completion_tokens,
            AuditRequest.quota,
            AuditRequest.model_name,
            AuditRequest.token_id,
            AuditRequest.token_name,
        ).where(request_user_filter(identity_key))
    ).all()
    models = Counter(model_name or "N/A" for _created_at, _prompt, _completion, _quota, model_name, _token_id, _token_name in rows)
    tokens = Counter(token_name or str(token_id or "unknown") for _created_at, _prompt, _completion, _quota, _model_name, token_id, token_name in rows)
    total_tokens = sum(prompt_tokens + completion_tokens for _created_at, prompt_tokens, completion_tokens, _quota, _model_name, _token_id, _token_name in rows)
    return {
        "request_count": len(rows),
        "total_tokens": total_tokens,
        "total_tokens_compact": _compact_number(total_tokens),
        "quota": sum(quota for _created_at, _prompt, _completion, quota, _model_name, _token_id, _token_name in rows),
        "top_model": models.most_common(1)[0][0] if models else "N/A",
        "top_token": tokens.most_common(1)[0][0] if tokens else "unknown",
        "first_seen_at": fmt_local(min((created_at for created_at, _prompt, _completion, _quota, _model_name, _token_id, _token_name in rows), default=None), tz_name),
        "last_seen_at": fmt_local(max((created_at for created_at, _prompt, _completion, _quota, _model_name, _token_id, _token_name in rows), default=None), tz_name),
    }


def _request_summary_load():
    return load_only(
        AuditRequest.request_id,
        AuditRequest.created_at,
        AuditRequest.user_id,
        AuditRequest.username,
        AuditRequest.token_id,
        AuditRequest.token_name,
        AuditRequest.model_name,
        AuditRequest.prompt_preview,
        AuditRequest.prompt_tokens,
        AuditRequest.completion_tokens,
        AuditRequest.quota,
    )


def _classification_summary_load():
    return load_only(
        AuditClassification.request_id,
        AuditClassification.category,
        AuditClassification.work_verdict,
        AuditClassification.confidence,
        AuditClassification.reason,
        AuditClassification.review_status,
    )


def _pagination(total: int, page: int, page_size: int) -> dict:
    total_pages = max((total + page_size - 1) // page_size, 1)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
    }


def _verdict_key_order() -> list[str]:
    return ["work", "non_work", "uncertain", "unclassified"]


def _verdict_options() -> list[dict]:
    return [{"key": key, "label": VERDICT_LABELS[key]} for key in _verdict_key_order()]


def _clean_preview(value: str | None) -> str:
    return " ".join((value or "").split())


def _short_preview(value: str | None, limit: int = 120) -> str:
    preview = _clean_preview(value)
    if len(preview) <= limit:
        return preview
    return preview[:limit].rstrip() + "..."


def _parse_user_filter(value: str) -> tuple[int | None, str | None]:
    if value.startswith("uid-"):
        return _safe_int(value.removeprefix("uid-")), None
    if value.startswith("name-"):
        from .audit_users import parse_identity_key

        return parse_identity_key(value)
    return None, value


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1
