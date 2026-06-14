import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .crypto import EncryptedText, encrypt_text, preview_text, sha256_text
from .models import AuditClassification, AuditDeadletter, AuditRequest, utc_now
from .schemas import ClassificationResult, RequestAuditEvent, UsageAuditEvent


def _relation_status(row: AuditRequest) -> str:
    if row.prompt_collected and row.usage_collected:
        return "complete"
    if row.prompt_collected:
        return "pending_usage"
    if row.usage_collected:
        return "pending_prompt"
    return "pending"


def _as_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _earliest(a, b):
    a = _as_utc(a)
    b = _as_utc(b)
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def get_or_create_request(session: Session, request_id: str) -> AuditRequest:
    row = session.scalar(select(AuditRequest).where(AuditRequest.request_id == request_id))
    if row is not None:
        return row
    row = AuditRequest(request_id=request_id, created_at=utc_now(), updated_at=utc_now())
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        row = session.scalar(select(AuditRequest).where(AuditRequest.request_id == request_id))
        if row is None:
            raise
    return row


def upsert_request_event(session: Session, event: RequestAuditEvent, prompt_key: bytes) -> AuditRequest:
    row = get_or_create_request(session, event.request_id)
    event_time = event.created_at or utc_now()
    row.request_event_at = event_time
    row.created_at = _earliest(row.created_at, event_time)
    row.user_id = event.user_id if event.user_id is not None else row.user_id
    row.username = event.username or row.username
    row.token_id = event.token_id if event.token_id is not None else row.token_id
    row.token_name = event.token_name or row.token_name
    row.model_name = event.model_name or row.model_name
    row.request_path = event.request_path or row.request_path
    row.relay_format = event.relay_format or row.relay_format
    row.is_stream = event.is_stream
    prompt_text = event.prompt_text or ""
    row.prompt_hash = event.prompt_hash or sha256_text(prompt_text)
    row.prompt_preview = event.prompt_preview or preview_text(prompt_text)
    row.prompt_len = event.prompt_len or len(prompt_text)
    row.prompt_omitted = event.prompt_omitted
    if event.prompt_omitted:
        row.prompt_nonce = None
        row.prompt_ciphertext = None
    else:
        encrypted = encrypt_text(prompt_text, prompt_key, event.request_id.encode("utf-8"))
        row.prompt_nonce = encrypted.nonce
        row.prompt_ciphertext = encrypted.ciphertext
    row.prompt_collected = True
    row.relation_status = _relation_status(row)
    row.updated_at = utc_now()
    session.flush()
    return row


def upsert_usage_event(session: Session, event: UsageAuditEvent) -> AuditRequest:
    row = get_or_create_request(session, event.request_id)
    event_time = event.created_at or utc_now()
    row.usage_event_at = event_time
    row.created_at = _earliest(row.created_at, event_time)
    row.user_id = event.user_id if event.user_id is not None else row.user_id
    row.username = event.username or row.username
    row.token_id = event.token_id if event.token_id is not None else row.token_id
    row.token_name = event.token_name or row.token_name
    row.model_name = event.model_name or row.model_name
    row.prompt_tokens = event.prompt_tokens
    row.completion_tokens = event.completion_tokens
    row.quota = event.quota
    row.channel_id = event.channel_id
    row.group = event.group or row.group
    row.use_time_seconds = event.use_time_seconds
    row.is_stream = event.is_stream
    row.upstream_request_id = event.upstream_request_id or row.upstream_request_id
    row.usage_collected = True
    row.relation_status = _relation_status(row)
    row.updated_at = utc_now()
    session.flush()
    return row


def write_deadletter(session: Session, event_type: str, reason: str, payload: bytes | str, request_id: str = "") -> None:
    if isinstance(payload, bytes):
        payload_text = payload.decode("utf-8", errors="replace")
    else:
        payload_text = payload
    session.add(
        AuditDeadletter(
            event_type=event_type,
            request_id=request_id,
            reason=reason,
            payload=payload_text[:20000],
        )
    )


def save_classification(session: Session, request_id: str, result: ClassificationResult, version: str) -> AuditClassification:
    row = session.scalar(select(AuditClassification).where(AuditClassification.request_id == request_id))
    if row is None:
        row = AuditClassification(request_id=request_id)
        session.add(row)
    row.category = result.category
    row.work_verdict = result.work_verdict
    row.confidence = max(0.0, min(1.0, result.confidence))
    row.reason = result.reason
    row.evidence = json.dumps(result.evidence, ensure_ascii=False)
    row.classifier_type = result.classifier_type
    row.classifier_version = version
    row.updated_at = utc_now()
    session.flush()
    return row


def update_review(session: Session, request_id: str, review_status: str, note: str, reviewed_by: str) -> AuditClassification:
    row = session.scalar(select(AuditClassification).where(AuditClassification.request_id == request_id))
    if row is None:
        row = AuditClassification(
            request_id=request_id,
            category="其他",
            work_verdict="uncertain",
            confidence=0.0,
            reason="未分类，人工直接复核",
            classifier_type="manual",
        )
        session.add(row)
    row.review_status = review_status
    row.review_note = note
    row.reviewed_by = reviewed_by
    row.reviewed_at = datetime.now(timezone.utc)
    row.updated_at = utc_now()
    session.flush()
    return row


def encrypted_text_from_row(row: AuditRequest) -> EncryptedText | None:
    if row.prompt_nonce is None or row.prompt_ciphertext is None:
        return None
    return EncryptedText(nonce=row.prompt_nonce, ciphertext=row.prompt_ciphertext)
