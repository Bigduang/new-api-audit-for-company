import base64
import os
from dataclasses import dataclass
from typing import Sequence


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _csv_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def normalize_key(raw: str) -> bytes:
    value = raw.strip()
    if value == "":
        raise ValueError("AUDIT_PROMPT_ENCRYPTION_KEY is required")
    if value.startswith("base64:"):
        key = base64.b64decode(value.removeprefix("base64:"))
    elif value.startswith("hex:"):
        key = bytes.fromhex(value.removeprefix("hex:"))
    else:
        key = value.encode("utf-8")
    if len(key) not in {16, 24, 32}:
        raise ValueError("AUDIT_PROMPT_ENCRYPTION_KEY must decode to 16, 24, or 32 bytes")
    return key


@dataclass(frozen=True)
class Settings:
    database_url: str
    audit_secret: str
    prompt_encryption_key: bytes
    signature_tolerance_seconds: int
    timezone: str
    classifier_version: str
    llm_enabled: bool
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout_seconds: int
    llm_min_confidence: float
    wecom_corpid: str
    wecom_appsecret: str
    wecom_agent_id: int
    public_base_url: str
    report_access_token: str
    retention_days: int
    max_body_bytes: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("AUDIT_DATABASE_URL", "sqlite:///./token_audit.db"),
            audit_secret=os.getenv("AUDIT_SECRET", ""),
            prompt_encryption_key=normalize_key(os.getenv("AUDIT_PROMPT_ENCRYPTION_KEY", "")),
            signature_tolerance_seconds=_int_env("AUDIT_SIGNATURE_TOLERANCE_SECONDS", 300),
            timezone=os.getenv("AUDIT_TIMEZONE", "Asia/Shanghai"),
            classifier_version=os.getenv("AUDIT_CLASSIFIER_VERSION", "rules-v1"),
            llm_enabled=_bool_env("AUDIT_LLM_ENABLED", False),
            llm_base_url=os.getenv("AUDIT_LLM_BASE_URL", "").rstrip("/"),
            llm_api_key=os.getenv("AUDIT_LLM_API_KEY", ""),
            llm_model=os.getenv("AUDIT_LLM_MODEL", ""),
            llm_timeout_seconds=_int_env("AUDIT_LLM_TIMEOUT_SECONDS", 20),
            llm_min_confidence=float(os.getenv("AUDIT_LLM_MIN_CONFIDENCE", "0.75")),
            wecom_corpid=os.getenv("WX_CORPID", ""),
            wecom_appsecret=os.getenv("WX_APPSECRET", ""),
            wecom_agent_id=_int_env("WX_AGENT_ID", 0),
            public_base_url=os.getenv("AUDIT_PUBLIC_BASE_URL", "").rstrip("/"),
            report_access_token=os.getenv("AUDIT_REPORT_ACCESS_TOKEN", ""),
            retention_days=_int_env("AUDIT_RETENTION_DAYS", 30),
            max_body_bytes=_int_env("AUDIT_MAX_BODY_BYTES", 2 * 1024 * 1024),
        )

    def validate_runtime(self) -> None:
        if not self.audit_secret:
            raise ValueError("AUDIT_SECRET is required")
        if self.llm_enabled and (not self.llm_base_url or not self.llm_api_key or not self.llm_model):
            raise ValueError("AUDIT_LLM_BASE_URL, AUDIT_LLM_API_KEY, and AUDIT_LLM_MODEL are required when AUDIT_LLM_ENABLED=true")


def settings_for_tests(
    *,
    database_url: str = "sqlite:///:memory:",
    audit_secret: str = "test-secret",
    prompt_key: bytes = b"0123456789abcdef0123456789abcdef",
    llm_enabled: bool = False,
    max_body_bytes: int = 2 * 1024 * 1024,
) -> Settings:
    return Settings(
        database_url=database_url,
        audit_secret=audit_secret,
        prompt_encryption_key=prompt_key,
        signature_tolerance_seconds=300,
        timezone="Asia/Shanghai",
        classifier_version="test-rules",
        llm_enabled=llm_enabled,
        llm_base_url="",
        llm_api_key="",
        llm_model="",
        llm_timeout_seconds=5,
        llm_min_confidence=0.75,
        wecom_corpid="",
        wecom_appsecret="",
        wecom_agent_id=0,
        public_base_url="",
        report_access_token="test-report-token",
        retention_days=30,
        max_body_bytes=max_body_bytes,
    )


def excluded_names_from_env() -> Sequence[str]:
    return _csv_env("AUDIT_EXCLUDED_TOKEN_NAMES")
