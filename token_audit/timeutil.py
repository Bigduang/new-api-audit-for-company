from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo


def parse_range(start: str | None, end: str | None, tz_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    if start:
        start_local = _parse_one(start, tz, beginning=True)
    else:
        start_local = datetime.combine(now.date(), time.min, tz)
    if end:
        end_local = _parse_one(end, tz, beginning=False)
    else:
        end_local = datetime.combine(now.date(), time.max, tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _parse_one(value: str, tz: ZoneInfo, *, beginning: bool) -> datetime:
    value = value.strip()
    if len(value) == 10:
        date_value = datetime.fromisoformat(value).date()
        return datetime.combine(date_value, time.min if beginning else time.max, tz)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed


def fmt_local(dt: datetime | None, tz_name: str) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S")

