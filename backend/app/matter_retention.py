"""资料准入和近期事件资格分别判断；来源日期不替代发生日期。"""

import re
from datetime import UTC, date, timedelta

from backend.app.matter_dates import occurrence

RETENTION_VERSION = "matter-retention-v1"


def temporal_status(matter, document, window_days, *, as_of=None):
    if matter.status in {"planned", "conditional", "committed"}:
        return "future_or_planned"
    observed = as_of or document.observed_at
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    cutoff = (observed - timedelta(days=window_days)).date()
    time = occurrence(matter.fields)
    occurred = time.get("iso")
    if not occurred and time.get("interval_end"):
        if date.fromisoformat(time["interval_end"]) < cutoff:
            return "historical"
        if date.fromisoformat(time["interval_start"]) > observed.date():
            return "future_or_planned"
    if occurred:
        day = date.fromisoformat(occurred)
        if day > observed.date():
            return "future_or_planned"
        if day < cutoff:
            return "historical"
    published = document.published_on
    if published and published < cutoff:
        return "historical"
    if published and published > observed.date() + timedelta(days=1):
        return "future_or_planned"
    if not occurred or not published:
        return "date_unknown"
    return "recent"


def source_channel(document, default_quality):
    content = str(document.payload.get("excerpt") or "")
    material = document.payload.get("content_extraction", {}).get("material_type")
    url = document.canonical_url
    if material == "generated_commentary" or re.search(
        r"人工智能生成|AI生成|AI投资人解读", content
    ):
        return "generated_commentary", "E"
    if material == "user_post" or re.search(r"/(?:user|users|community|forum|ugc)/", url):
        return "user_post", "D"
    if material == "syndicated" or "转载自" in content:
        return "syndicated", "C"
    if default_quality == "A":
        return "official_publication", "A"
    if material == "staff_report":
        return "staff_report", "B"
    return "unclassified_public_page", "C"
