from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AuditRequest(Base):
    __tablename__ = "audit_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    request_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    usage_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    username: Mapped[str] = mapped_column(String(128), default="", index=True)
    token_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    token_name: Mapped[str] = mapped_column(String(255), default="", index=True)
    model_name: Mapped[str] = mapped_column(String(255), default="", index=True)
    request_path: Mapped[str] = mapped_column(String(255), default="")
    relay_format: Mapped[str] = mapped_column(String(64), default="")
    is_stream: Mapped[bool] = mapped_column(Boolean, default=False)

    prompt_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    prompt_preview: Mapped[str] = mapped_column(Text, default="")
    prompt_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    prompt_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    prompt_len: Mapped[int] = mapped_column(Integer, default=0)
    prompt_collected: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    prompt_omitted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    quota: Mapped[int] = mapped_column(Integer, default=0)
    channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    group: Mapped[str] = mapped_column("group_name", String(128), default="")
    use_time_seconds: Mapped[int] = mapped_column(Integer, default=0)
    upstream_request_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    usage_collected: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    relation_status: Mapped[str] = mapped_column(String(32), default="pending_usage", index=True)


class AuditClassification(Base):
    __tablename__ = "audit_classifications"
    __table_args__ = (UniqueConstraint("request_id", name="uq_audit_classifications_request_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(96), index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    work_verdict: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    classifier_type: Mapped[str] = mapped_column(String(32), default="rules")
    classifier_version: Mapped[str] = mapped_column(String(64), default="")
    review_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[str] = mapped_column(String(128), default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AuditDeadletter(Base):
    __tablename__ = "audit_events_deadletter"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), default="", index=True)
    request_id: Mapped[str] = mapped_column(String(96), default="", index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class AuditUserWorkSummary(Base):
    __tablename__ = "audit_user_work_summaries"
    __table_args__ = (UniqueConstraint("period_start", "period_end", "username", name="uq_work_summary_period_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    username: Mapped[str] = mapped_column(String(128), default="", index=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    confidence_overall: Mapped[str] = mapped_column(String(32), default="")
    summary_json: Mapped[str] = mapped_column(Text, default="")
    summary_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AuditDailyReport(Base):
    __tablename__ = "audit_daily_reports"
    __table_args__ = (UniqueConstraint("period_start", "period_end", name="uq_daily_report_period"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_date: Mapped[str] = mapped_column(String(10), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    summary_json: Mapped[str] = mapped_column(Text, default="")
    html_snapshot: Mapped[str] = mapped_column(Text, default="")
    wecom_description: Mapped[str] = mapped_column(Text, default="")
    wecom_msgid: Mapped[str] = mapped_column(String(255), default="")
    wecom_response: Mapped[str] = mapped_column(Text, default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
