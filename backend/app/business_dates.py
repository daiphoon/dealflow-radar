"""固定研究日期口径；不改变实际运行时点或历史存档。"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE = "Asia/Shanghai"
SHANGHAI = ZoneInfo(BUSINESS_TIMEZONE)


def business_date(value: date | datetime | str) -> date:
    if isinstance(value, str):
        value = date.fromisoformat(value) if len(value) == 10 else datetime.fromisoformat(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reference_timezone_required")
        return value.astimezone(SHANGHAI).date()
    if isinstance(value, date):
        return value
    raise ValueError("invalid_business_reference")


def reference_fields(value: date | datetime | str, days: int) -> dict:
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3660:
        raise ValueError("invalid_event_window_days")
    end = business_date(value)
    return {
        "business_reference_date": end.isoformat(),
        "business_timezone": BUSINESS_TIMEZONE,
        "event_window_start": (end - timedelta(days=days)).isoformat(),
        "event_window_end": end.isoformat(),
        "event_window_days": days,
    }


def report_reference(coverage: dict, generated_at: datetime) -> tuple[date, str, int]:
    """旧记录读时兼容并明确 unknown；冻结执行使用更严格的入口。"""
    try:
        fields = reference_fields(
            coverage.get("reference_at"), coverage.get("event_window_days", 365)
        )
        if any(coverage.get(key, expected) != expected for key, expected in fields.items()):
            raise ValueError("stored_reference_fields_conflict")
        return (
            date.fromisoformat(fields["business_reference_date"]),
            "研究记录",
            fields["event_window_days"],
        )
    except (ValueError, TypeError):
        return business_date(generated_at), "研究参考日未记录或无效；按本次生成日列示", 365
