from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .classifier import classify_by_rules
from .config import Settings
from .crypto import decrypt_text
from .llm_classifier import classify_with_llm
from .models import AuditClassification, AuditDailyReport, AuditDeadletter, AuditRequest, AuditUserWorkSummary
from .repository import encrypted_text_from_row, save_classification


def classify_requests(session: Session, settings: Settings, start: datetime, end: datetime, *, force: bool = False) -> int:
    query = select(AuditRequest).where(AuditRequest.created_at >= start, AuditRequest.created_at <= end)
    if not force:
        query = query.outerjoin(AuditClassification, AuditClassification.request_id == AuditRequest.request_id).where(AuditClassification.id.is_(None))
    rows = session.scalars(query.order_by(AuditRequest.created_at.asc())).all()
    count = 0
    for row in rows:
        encrypted = encrypted_text_from_row(row)
        prompt_text = ""
        if encrypted is not None:
            prompt_text = decrypt_text(encrypted, settings.prompt_encryption_key, row.request_id.encode("utf-8"))
        result = classify_by_rules(prompt_text or row.prompt_preview)
        if result.needs_llm:
            llm_result = classify_with_llm(settings, row.prompt_preview, prompt_text)
            if llm_result is not None and llm_result.confidence >= settings.llm_min_confidence:
                result = llm_result
        save_classification(session, row.request_id, result, settings.classifier_version)
        count += 1
    return count


def cleanup_old_audit_data(session: Session, settings: Settings) -> dict[str, int]:
    if settings.retention_days <= 0:
        return {"requests": 0, "classifications": 0, "deadletters": 0, "work_summaries": 0, "daily_reports": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
    old_request_ids = select(AuditRequest.request_id).where(AuditRequest.created_at < cutoff)
    classifications = session.execute(
        delete(AuditClassification).where(AuditClassification.request_id.in_(old_request_ids))
    ).rowcount
    requests = session.execute(delete(AuditRequest).where(AuditRequest.created_at < cutoff)).rowcount
    deadletters = session.execute(delete(AuditDeadletter).where(AuditDeadletter.created_at < cutoff)).rowcount
    work_summaries = session.execute(delete(AuditUserWorkSummary).where(AuditUserWorkSummary.period_end < cutoff)).rowcount
    daily_reports = session.execute(delete(AuditDailyReport).where(AuditDailyReport.period_end < cutoff)).rowcount
    return {
        "requests": requests or 0,
        "classifications": classifications or 0,
        "deadletters": deadletters or 0,
        "work_summaries": work_summaries or 0,
        "daily_reports": daily_reports or 0,
    }
