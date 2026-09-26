"""从已经通过权限与有效证据过滤的投影计算语义版本。"""

import re

from backend.app.evidence_integrity import hash_canonical_object
from backend.app.matter_validation import normalize_amount

VERSION = "visible-semantic-v1"


def canonical_fact(fact):
    value = str(fact.get("value", ""))
    amount = normalize_amount(value)
    if amount.get("amount") is not None:
        value = {k: v for k, v in amount.items() if k != "raw"}
        # normalize_amount 已用 Decimal 展开倍率；仅消除小数末尾零，不舍入或改原文。
        if "." in value["amount"]:
            value["amount"] = value["amount"].rstrip("0").rstrip(".")
    if "投资方" in str(fact.get("name", "")):
        value = sorted(
            {s.strip() for s in re.split(r"[、,，]", str(fact.get("value", ""))) if s.strip()}
        )
    return {"name": fact.get("name"), "value": value, "unit": fact.get("unit")}


def _canonical_set(items):
    unique = {hash_canonical_object(item): item for item in items}
    return [unique[key] for key in sorted(unique)]


def semantic_event(event):
    data = (
        event.model_dump(mode="json", exclude={"semantic_version"})
        if hasattr(event, "model_dump")
        else event
    )
    facts = _canonical_set(canonical_fact(fact) for fact in data.get("facts", []))
    return {
        "version": VERSION,
        **{
            key: data.get(key)
            for key in (
                "event_type",
                "event_subtype",
                "status",
                "occurred_on",
                "occurred_at",
                "date_precision",
                "direction",
                "risk_severity",
                "information_status",
                "fact_status",
            )
        },
        "facts": facts,
        "unstructured_fact": data.get("summary") if not facts else None,
        "curated_context": _canonical_set(
            {
                k: v.get(k)
                for k in ("date_precision", "date_basis", "occurred_date_text", "subject_scope")
            }
            for v in data.get("curated_versions", [])
            if v.get("is_current")
        ),
        "field_states": _canonical_set(
            {**canonical_fact(f), "support_status": f.get("support_status")}
            for f in data.get("fact_ledger", [])
        ),
    }


def event_version(event):
    return hash_canonical_object(semantic_event(event))


def content_version(events):
    # 转载、证据 ID 和处理版本不进入业务变化判断。
    return hash_canonical_object(sorted({event_version(e) for e in events}))
