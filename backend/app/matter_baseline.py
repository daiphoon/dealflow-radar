"""只读桥接既有人工/融资契约；不改人工确认值，也不补造缺失事实。"""

from backend.app.matter_dates import normalize_date
from backend.app.research_matters import Matter, classification, infer_fields

VERSION = "baseline-adapter-v1"


def curated_matters(event, data, subject):
    if data.get("facts") != event.facts:
        return []
    scope_text = data.get("subject_scope", "")
    if "品牌" in scope_text:
        brands = [n for n in subject.aliases if n not in subject.legal_aliases]
        mentioned = [n for n in brands if n in event.title + event.summary]
        if len(mentioned) == 1:
            name = mentioned[0]
        elif len(brands) == 1:
            name = brands[0]
        else:
            return []
        scope = f"brand:{name}"
    elif any(v in scope_text for v in ("集团", "子公司", "母公司")):
        return []
    else:
        name, scope = subject.legal_name, "legal_entity"
    action = event.title + "，" + event.summary
    classified = classification(action)
    if not classified:
        return []
    category, subtype = classified
    # 旧表只有标题/摘要时，明确记录降级；已有类型字段优先于文本推导。
    fields = infer_fields(action, subtype)
    for key in ("date", "occurred", "disclosed", "planned"):
        fields.pop(key, None)
    labels = {
        "融资金额": "financing",
        "公开融资金额（原口径）": "financing",
        "融资轮次": "round",
        "明确投资方": "investors",
        "交易标识": "transaction_id",
        "项目标识": "project_id",
        "上市市场": "market",
    }
    for fact in data.get("facts", []):
        role = labels.get(fact.get("name"))
        if role and fact.get("value"):
            fields[role] = {
                "value": fact["value"],
                "role": role,
                "quote": "",
                "basis": "curator_confirmed_field",
                "adapter_version": VERSION,
            }
    for role, raw in (
        ("occurred", data.get("occurred_date_text", "")),
        (
            "disclosed",
            data.get("date_text", "")
            if "报道" in data.get("date_basis", "") or "披露" in data.get("date_basis", "")
            else "",
        ),
    ):
        parsed = normalize_date(raw)
        if parsed.get("interval_start"):
            fields[role] = {
                "value": raw,
                "role": role,
                **parsed,
                "basis": "curator_confirmed_date",
                "adapter_version": VERSION,
            }
    return [
        Matter(
            category,
            subtype,
            name,
            scope,
            action,
            "reported",
            fields,
            ["curated_baseline", "legacy_text_field_fallback"],
        )
    ]


def financing_matter(old):
    f = old.fields
    fields = {}
    for role, value in (
        ("round", f.round),
        ("financing", f.amount_text),
        ("investors", "、".join(f.investors)),
        ("occurred", f.occurred_on),
        ("disclosed", f.disclosed_on),
    ):
        if value:
            value = str(value)
            fields[role] = {
                "value": value,
                "quote": old.excerpt,
                "role": role,
                "adapter_version": VERSION,
            }
            if role in {"occurred", "disclosed"}:
                fields[role].update(normalize_date(value))
    scope = "legal_entity" if f.subject_scope.startswith("legal_entity:") else f.subject_scope
    return Matter(
        "financing_cap_table",
        "company_financing",
        f.subject_name,
        scope,
        old.excerpt,
        "reported",
        fields,
        ["legacy_financing_baseline"],
    )
