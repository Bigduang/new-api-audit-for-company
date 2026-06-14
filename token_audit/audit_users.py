from __future__ import annotations

import base64
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm import load_only

from .models import AuditClassification, AuditRequest, AuditUser, utc_now


def identity_key_for(user_id: int | None, username: str | None) -> str:
    if user_id is not None:
        return f"uid-{user_id}"
    name = (username or "").strip()
    if name:
        encoded = base64.urlsafe_b64encode(name.encode("utf-8")).decode("ascii").rstrip("=")
        return f"name-{encoded}"
    return "unknown"


def parse_identity_key(identity_key: str) -> tuple[int | None, str | None]:
    if identity_key.startswith("uid-"):
        return int(identity_key.removeprefix("uid-")), None
    if identity_key.startswith("name-"):
        encoded = identity_key.removeprefix("name-")
        padding = "=" * (-len(encoded) % 4)
        return None, base64.urlsafe_b64decode((encoded + padding).encode("ascii")).decode("utf-8")
    if identity_key == "unknown":
        return None, ""
    raise ValueError("invalid user identity")


def request_identity_key(row: AuditRequest) -> str:
    return identity_key_for(row.user_id, row.username)


def request_user_filter(identity_key: str):
    user_id, username = parse_identity_key(identity_key)
    if user_id is not None:
        return AuditRequest.user_id == user_id
    if username:
        return and_(AuditRequest.user_id.is_(None), AuditRequest.username == username)
    return and_(AuditRequest.user_id.is_(None), or_(AuditRequest.username.is_(None), AuditRequest.username == ""))


def display_name_for(user: AuditUser | None, user_id: int | None, username: str | None) -> str:
    if user is not None and user.display_name.strip():
        return user.display_name.strip()
    if username:
        return username
    if user_id is not None:
        return str(user_id)
    return "unknown"


def load_audit_user_map(session: Session) -> dict[str, AuditUser]:
    rows = session.scalars(select(AuditUser)).all()
    return {row.identity_key: row for row in rows}


def enabled_audit_user_map(session: Session) -> dict[str, AuditUser]:
    rows = session.scalars(select(AuditUser).where(AuditUser.audit_enabled.is_(True))).all()
    return {row.identity_key: row for row in rows}


def is_request_audit_enabled(row: AuditRequest, users: dict[str, AuditUser]) -> bool:
    user = users.get(request_identity_key(row))
    return bool(user and user.audit_enabled)


def sync_audit_users_from_requests(session: Session) -> int:
    seen: dict[str, dict[str, object]] = {}
    rows = session.execute(
        select(AuditRequest.user_id, AuditRequest.username, AuditRequest.created_at).order_by(AuditRequest.created_at.asc())
    ).all()
    for user_id, username, created_at in rows:
        key = identity_key_for(user_id, username)
        data = seen.setdefault(
            key,
            {
                "user_id": user_id,
                "username": username or "",
                "first_seen_at": created_at,
                "last_seen_at": created_at,
            },
        )
        if created_at is not None:
            if data["first_seen_at"] is None or created_at < data["first_seen_at"]:
                data["first_seen_at"] = created_at
            if data["last_seen_at"] is None or created_at >= data["last_seen_at"]:
                data["last_seen_at"] = created_at
                data["username"] = username or data["username"] or ""
        if data["user_id"] is None and user_id is not None:
            data["user_id"] = user_id

    created = 0
    now = utc_now()
    for key, data in seen.items():
        row = session.scalar(select(AuditUser).where(AuditUser.identity_key == key))
        if row is None:
            row = AuditUser(identity_key=key, audit_enabled=False, created_at=now)
            created += 1
        row.user_id = data["user_id"]
        row.username = str(data["username"] or "")
        row.first_seen_at = data["first_seen_at"]
        row.last_seen_at = data["last_seen_at"]
        row.updated_at = now
        session.add(row)
    session.flush()
    return created


def update_audit_user(
    session: Session,
    identity_key: str,
    *,
    display_name: str | None = None,
    audit_enabled: bool | None = None,
    notes: str | None = None,
) -> AuditUser:
    row = session.scalar(select(AuditUser).where(AuditUser.identity_key == identity_key))
    if row is None:
        user_id, username = parse_identity_key(identity_key)
        row = AuditUser(identity_key=identity_key, user_id=user_id, username=username or "")
    if display_name is not None:
        row.display_name = display_name.strip()[:128]
    if audit_enabled is not None:
        row.audit_enabled = audit_enabled
    if notes is not None:
        row.notes = notes.strip()[:2000]
    row.updated_at = utc_now()
    session.add(row)
    session.flush()
    return row


@dataclass(frozen=True)
class AuditUserListItem:
    identity_key: str
    user_id: int | None
    username: str
    display_name: str
    audit_enabled: bool
    notes: str
    request_count: int
    total_tokens: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    top_model: str
    configured: bool


def list_audit_users(session: Session, *, status: str = "all", configured: str = "all", query: str = "") -> list[AuditUserListItem]:
    users = load_audit_user_map(session)
    stats: dict[str, dict[str, object]] = {}
    token_expr = AuditRequest.prompt_tokens + AuditRequest.completion_tokens
    rows = session.execute(
        select(
            AuditRequest.user_id,
            AuditRequest.username,
            func.count(AuditRequest.id),
            func.sum(token_expr),
            func.min(AuditRequest.created_at),
            func.max(AuditRequest.created_at),
        )
        .group_by(AuditRequest.user_id, AuditRequest.username)
    ).all()
    for user_id, username, request_count, total_tokens, first_seen_at, last_seen_at in rows:
        key = identity_key_for(user_id, username)
        data = stats.setdefault(
            key,
            {
                "request_count": 0,
                "total_tokens": 0,
                "first_seen_at": first_seen_at,
                "last_seen_at": last_seen_at,
                "models": Counter(),
            },
        )
        data["request_count"] = int(data["request_count"]) + int(request_count or 0)
        data["total_tokens"] = int(data["total_tokens"]) + int(total_tokens or 0)
        if first_seen_at is not None and (data["first_seen_at"] is None or first_seen_at < data["first_seen_at"]):
            data["first_seen_at"] = first_seen_at
        if last_seen_at is not None and (data["last_seen_at"] is None or last_seen_at > data["last_seen_at"]):
            data["last_seen_at"] = last_seen_at

    model_rows = session.execute(
        select(
            AuditRequest.user_id,
            AuditRequest.username,
            AuditRequest.model_name,
            func.count(AuditRequest.id),
        ).group_by(AuditRequest.user_id, AuditRequest.username, AuditRequest.model_name)
    ).all()
    for user_id, username, model_name, model_count in model_rows:
        key = identity_key_for(user_id, username)
        data = stats.setdefault(
            key,
            {
                "request_count": 0,
                "total_tokens": 0,
                "first_seen_at": None,
                "last_seen_at": None,
                "models": Counter(),
            },
        )
        data["models"][model_name or "N/A"] += int(model_count or 0)

    items: list[AuditUserListItem] = []
    for key, user in users.items():
        data = stats.get(key, {})
        models = data.get("models") or Counter()
        top_model = models.most_common(1)[0][0] if models else "N/A"
        display_name = user.display_name.strip()
        item = AuditUserListItem(
            identity_key=key,
            user_id=user.user_id,
            username=user.username,
            display_name=display_name,
            audit_enabled=user.audit_enabled,
            notes=user.notes,
            request_count=int(data.get("request_count") or 0),
            total_tokens=int(data.get("total_tokens") or 0),
            first_seen_at=user.first_seen_at or data.get("first_seen_at"),
            last_seen_at=user.last_seen_at or data.get("last_seen_at"),
            top_model=top_model,
            configured=bool(display_name),
        )
        if status == "enabled" and not item.audit_enabled:
            continue
        if status == "disabled" and item.audit_enabled:
            continue
        if configured == "configured" and not item.configured:
            continue
        if configured == "unconfigured" and item.configured:
            continue
        if query:
            haystack = f"{item.user_id or ''} {item.username} {item.display_name} {item.notes}".lower()
            if query.lower() not in haystack:
                continue
        items.append(item)
    return sorted(items, key=lambda item: (item.last_seen_at is None, item.last_seen_at), reverse=True)


def get_audit_user(session: Session, identity_key: str) -> AuditUser:
    row = session.scalar(select(AuditUser).where(AuditUser.identity_key == identity_key))
    if row is None:
        raise LookupError("audit user not found")
    return row


def user_request_history(
    session: Session,
    identity_key: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    verdict: str = "",
    token: str = "",
    model: str = "",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[tuple[AuditRequest, AuditClassification | None]], int]:
    conditions = [request_user_filter(identity_key)]
    if start is not None:
        conditions.append(AuditRequest.created_at >= start)
    if end is not None:
        conditions.append(AuditRequest.created_at <= end)
    if token:
        conditions.append(or_(AuditRequest.token_name == token, AuditRequest.token_id == _safe_int(token)))
    if model:
        conditions.append(AuditRequest.model_name == model)
    query = (
        select(AuditRequest, AuditClassification)
        .outerjoin(AuditClassification, AuditClassification.request_id == AuditRequest.request_id)
        .options(_request_summary_load(), _classification_summary_load())
        .where(*conditions)
    )
    if verdict:
        query = query.where(AuditClassification.work_verdict == verdict)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.execute(
        query.order_by(AuditRequest.created_at.desc()).offset(max(page - 1, 0) * page_size).limit(page_size)
    ).all()
    return rows, int(total)


def user_filter_options(session: Session, identity_key: str) -> dict[str, list[str]]:
    rows = session.execute(
        select(AuditRequest.token_id, AuditRequest.token_name, AuditRequest.model_name).where(request_user_filter(identity_key))
    ).all()
    tokens = sorted({token_name or str(token_id or "") for token_id, token_name, _model_name in rows if token_name or token_id})
    models = sorted({model_name for _token_id, _token_name, model_name in rows if model_name})
    return {"tokens": tokens, "models": models}


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


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return -1
