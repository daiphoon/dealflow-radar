"""动作日期、披露日期与计划日期独立；旧 date 只读适配，不推断缺失年份。"""

import calendar
import re
from datetime import date

DATE_VERSION = "action-time-v2"
DATE_PATTERN = re.compile(
    r"(?<!\d)(?:20\d{2}年(?:\d{1,2}月(?:\d{1,2}日)?)?"
    r"|20\d{2}[-/]\d{1,2}(?:[-/]\d{1,2})?|\d{1,2}月\d{1,2}日)"
)


def normalize_date(value):
    parts = [int(n) for n in re.findall(r"\d+", value)]
    result = {"raw": value, "parser_version": DATE_VERSION, "precision": "unknown"}
    if not parts or parts[0] < 2000:
        result["precision"] = "year_unknown"
        return result
    if len(parts) not in {1, 2, 3}:
        return result
    try:
        year = parts[0]
        month = parts[1] if len(parts) > 1 else 1
        day = parts[2] if len(parts) > 2 else 1
        start = date(year, month, day)
        end = (
            start
            if len(parts) == 3
            else date(year, month, calendar.monthrange(year, month)[1])
            if len(parts) == 2
            else date(year, 12, 31)
        )
    except ValueError:
        return result
    result.update(
        precision={1: "year", 2: "month", 3: "day"}[len(parts)],
        interval_start=start.isoformat(),
        interval_end=end.isoformat(),
    )
    if len(parts) == 3:
        result["iso"] = start.isoformat()
    return result


def date_role(action, match, subtype):
    from backend.app.matter_validation import action_supported

    start = max(action.rfind(c, 0, match.start()) for c in "，,。；;：:\n") + 1
    ends = [action.find(c, match.end()) for c in "，,。；;：:\n"]
    end = min((v for v in ends if v >= 0), default=len(action))
    before = action[start : match.start()]
    local = action[start:end]
    if re.search(r"继|此前|曾于|早在", before) and action_supported(action[:start], subtype):
        return None
    if re.search(r"报道|披露|发布于|发布时间|公告日期|消息|日电", local):
        return "disclosed"
    if re.search(r"拟|计划|预计|将于|将", before):
        return "planned"
    # 后置时间须明确回指发生动作，不能把后来签署的协议日归给先前融资。
    if action_supported(local, subtype) or (
        re.search(r"完成时间|发生时间|完成于|发生于", before)
        and action_supported(action[:start], subtype)
    ):
        if re.search(r"拟|计划|预计", local):
            return "planned"
        return "occurred"
    return None


def date_fields(action, subtype):
    fields = {}
    ambiguous = set()
    for match in DATE_PATTERN.finditer(action):
        role = date_role(action, match, subtype)
        if role is None or role in ambiguous:
            continue
        item = {
            "value": match.group(),
            "quote": action,
            "role": role,
            **normalize_date(match.group()),
            "date_start": match.start(),
            "date_end": match.end(),
        }
        if role in fields and fields[role]["value"] != item["value"]:
            fields.pop(role)
            ambiguous.add(role)
        else:
            fields[role] = item
    if "occurred" in fields:
        fields["date"] = dict(fields["occurred"])
    return fields


def occurrence(fields):
    if "occurred" in fields:
        return fields["occurred"]
    old = fields.get("date", {})
    # Legacy iso-only values explicitly represented an occurrence; other semantics stay unknown.
    return old if old.get("role") == "occurred" or ("iso" in old and "role" not in old) else {}
