from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def ensure_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class RequestAuditEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_id: str = Field(min_length=1, max_length=96)
    created_at: datetime | None = None
    user_id: int | None = None
    username: str = Field(default="", max_length=128)
    token_id: int | None = None
    token_name: str = Field(default="", max_length=255)
    model_name: str = Field(default="", max_length=255)
    request_path: str = Field(default="", max_length=255)
    relay_format: str = Field(default="", max_length=64)
    is_stream: bool = False
    prompt_hash: str = Field(default="", max_length=64)
    prompt_preview: str = ""
    prompt_text: str = ""
    prompt_len: int = Field(default=0, ge=0)
    prompt_omitted: bool = False

    @field_validator("created_at", mode="after")
    @classmethod
    def normalize_created_at(cls, value: datetime | None) -> datetime:
        return ensure_utc(value)


class UsageAuditEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_id: str = Field(min_length=1, max_length=96)
    created_at: datetime | None = None
    user_id: int | None = None
    username: str = Field(default="", max_length=128)
    token_id: int | None = None
    token_name: str = Field(default="", max_length=255)
    model_name: str = Field(default="", max_length=255)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    quota: int = Field(default=0, ge=0)
    channel_id: int | None = None
    group: str = Field(default="", max_length=128)
    use_time_seconds: int = Field(default=0, ge=0)
    is_stream: bool = False
    upstream_request_id: str = Field(default="", max_length=128)

    @field_validator("created_at", mode="after")
    @classmethod
    def normalize_created_at(cls, value: datetime | None) -> datetime:
        return ensure_utc(value)


class ReviewPatch(BaseModel):
    review_status: Literal["pending", "confirmed", "false_positive", "ignored"]
    review_note: str = ""
    reviewed_by: str = ""


class ClassificationResult(BaseModel):
    category: str
    work_verdict: Literal["work", "non_work", "uncertain"]
    confidence: float
    reason: str
    evidence: list[str] = Field(default_factory=list)
    classifier_type: str = "rules"
    needs_llm: bool = False
