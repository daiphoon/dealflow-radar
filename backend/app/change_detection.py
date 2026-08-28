from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

CHANGE_POLICY_VERSION = "investor-material-change-v1"
MATERIAL_CHANGE_MIN_SCORE = 60


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_records(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _percentage(value: object) -> float | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return float(text.removesuffix("%"))
    except ValueError:
        return None


@dataclass(frozen=True)
class DetectedChange:
    module_code: str
    change_key: str
    change_type: str
    field_label: str
    before_value: str | None
    after_value: str
    title: str
    summary: str
    event_type: str
    direction: str
    materiality_score: int
    risk_severity: str = "none"
    uncertainties: tuple[str, ...] = field(default_factory=tuple)
    publishable: bool = True

    @property
    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "policy_version": CHANGE_POLICY_VERSION,
            "module_code": self.module_code,
            "change_key": self.change_key,
            "change_type": self.change_type,
            "before_value": self.before_value,
            "after_value": self.after_value,
        }

    @property
    def fingerprint(self) -> str:
        return _sha256(self.fingerprint_payload)


def _registration_changes(
    previous: dict[str, Any], current: dict[str, Any]
) -> list[DetectedChange]:
    previous_fields = _as_dict(previous.get("fields"))
    current_fields = _as_dict(current.get("fields"))
    definitions = {
        "registration_status": ("登记状态", 85, "governance_people"),
        "legal_representative": ("法定代表人", 75, "governance_people"),
        "registered_capital": ("注册资本", 70, "financing_cap_table"),
        "registration_authority": ("登记机关", 55, "governance_people"),
    }
    changes: list[DetectedChange] = []
    for key, (label, score, event_type) in definitions.items():
        before = _text(previous_fields.get(key))
        after = _text(current_fields.get(key))
        if before is None or after is None or before == after:
            continue
        changes.append(
            DetectedChange(
                module_code="company_base",
                change_key=f"registration:{key}",
                change_type="field_changed",
                field_label=label,
                before_value=before,
                after_value=after,
                title=f"{label}发生变化",
                summary=f"授权工商资料显示，{label}由“{before}”变为“{after}”。",
                event_type=event_type,
                direction="neutral",
                materiality_score=score,
            )
        )
    return changes


def _shareholder_changes(previous: dict[str, Any], current: dict[str, Any]) -> list[DetectedChange]:
    previous_records = {
        str(item.get("key")): item
        for item in _as_records(previous.get("records"))
        if item.get("key")
    }
    current_records = {
        str(item.get("key")): item
        for item in _as_records(current.get("records"))
        if item.get("key")
    }
    changes: list[DetectedChange] = []
    for key in sorted(previous_records.keys() & current_records.keys()):
        before_item = previous_records[key]
        after_item = current_records[key]
        before = _text(_as_dict(before_item.get("fields")).get("shareholding_ratio"))
        after = _text(_as_dict(after_item.get("fields")).get("shareholding_ratio"))
        if before is None or after is None or before == after:
            continue
        before_number = _percentage(before)
        after_number = _percentage(after)
        delta = (
            abs(after_number - before_number)
            if before_number is not None and after_number is not None
            else None
        )
        crosses_control_threshold = bool(
            before_number is not None
            and after_number is not None
            and any(
                (before_number < threshold <= after_number)
                or (after_number < threshold <= before_number)
                for threshold in (5, 20, 30, 50)
            )
        )
        score = 85 if crosses_control_threshold else 75 if delta is None or delta >= 1 else 55
        holder_name = _text(after_item.get("label")) or key
        changes.append(
            DetectedChange(
                module_code="company_base",
                change_key=f"shareholder:{key}:ratio",
                change_type="shareholding_ratio_changed",
                field_label=f"{holder_name}的工商登记持股比例",
                before_value=before,
                after_value=after,
                title="工商登记持股比例发生变化",
                summary=(
                    f"授权工商资料显示，股东“{holder_name}”的工商登记持股比例"
                    f"由 {before} 变为 {after}。"
                ),
                event_type="financing_cap_table",
                direction="neutral",
                materiality_score=score,
            )
        )

    # 只有两次快照都覆盖完整股东清单时，才可以把缺失记录解释为新增或退出。
    if bool(previous.get("complete")) and bool(current.get("complete")):
        for key in sorted(current_records.keys() - previous_records.keys()):
            item = current_records[key]
            holder_name = _text(item.get("label")) or key
            ratio = _text(_as_dict(item.get("fields")).get("shareholding_ratio"))
            after = holder_name if ratio is None else f"{holder_name}（{ratio}）"
            changes.append(
                DetectedChange(
                    module_code="company_base",
                    change_key=f"shareholder:{key}:added",
                    change_type="shareholder_added",
                    field_label="新增工商登记股东",
                    before_value=None,
                    after_value=after,
                    title="工商登记股东发生新增",
                    summary=f"完整股东快照显示，新增工商登记股东“{holder_name}”。",
                    event_type="financing_cap_table",
                    direction="neutral",
                    materiality_score=75,
                )
            )
        for key in sorted(previous_records.keys() - current_records.keys()):
            item = previous_records[key]
            holder_name = _text(item.get("label")) or key
            changes.append(
                DetectedChange(
                    module_code="company_base",
                    change_key=f"shareholder:{key}:removed",
                    change_type="shareholder_removed",
                    field_label="退出工商登记股东",
                    before_value=holder_name,
                    after_value="不再出现在当前完整工商股东快照中",
                    title="工商登记股东发生退出",
                    summary=f"完整股东快照显示，股东“{holder_name}”不再出现在当前清单中。",
                    event_type="financing_cap_table",
                    direction="neutral",
                    materiality_score=75,
                )
            )
    return changes


def _history_changes(previous: dict[str, Any], current: dict[str, Any]) -> list[DetectedChange]:
    previous_keys = {
        str(item.get("key")) for item in _as_records(previous.get("records")) if item.get("key")
    }
    changes: list[DetectedChange] = []
    for item in _as_records(current.get("records")):
        key = str(item.get("key") or "")
        if not key or key in previous_keys:
            continue
        label = _text(item.get("label")) or "工商变更"
        fields = _as_dict(item.get("fields"))
        before = _text(fields.get("before"))
        after = _text(fields.get("after")) or "已更新"
        score = 45
        event_type = "governance_people"
        if any(keyword in label for keyword in ("股东", "出资", "投资人", "股权")):
            score = 80
            event_type = "financing_cap_table"
        elif any(keyword in label for keyword in ("法定代表人", "董事", "监事", "经理")):
            score = 75
        elif "注册资本" in label:
            score = 70
            event_type = "financing_cap_table"
        elif any(keyword in label for keyword in ("登记状态", "经营状态")):
            score = 85
        elif "经营范围" in label:
            score = 55
        changes.append(
            DetectedChange(
                module_code="history",
                change_key=f"history:{key}",
                change_type="registration_history_added",
                field_label=label,
                before_value=before,
                after_value=after,
                title=f"{label}发生工商变更",
                summary=(
                    f"授权工商变更资料新增“{label}”记录"
                    + (f"，由“{before}”变为“{after}”。" if before else f"，当前内容为“{after}”。")
                ),
                event_type=event_type,
                direction="neutral",
                materiality_score=score,
            )
        )
    return changes


def _operation_changes(previous: dict[str, Any], current: dict[str, Any]) -> list[DetectedChange]:
    previous_keys = {
        str(item.get("key")) for item in _as_records(previous.get("records")) if item.get("key")
    }
    changes: list[DetectedChange] = []
    for item in _as_records(current.get("records")):
        key = str(item.get("key") or "")
        if not key or key in previous_keys:
            continue
        label = _text(item.get("label")) or "经营公示记录"
        fields = _as_dict(item.get("fields"))
        amount = _text(fields.get("amount"))
        role = _text(fields.get("enterprise_identity"))
        score = 65 if amount or role in {"中标人", "成交供应商"} else 50
        after_parts = [part for part in (amount, role, _text(fields.get("published_on"))) if part]
        after = "；".join(after_parts) or "新增记录"
        changes.append(
            DetectedChange(
                module_code="operation",
                change_key=f"operation:{key}",
                change_type="operation_record_added",
                field_label="新增招投标或经营公示",
                before_value=None,
                after_value=after,
                title="新增经营公示记录",
                summary=f"授权来源新增“{label}”记录。",
                event_type="contract_commercial",
                direction="positive" if role in {"中标人", "成交供应商"} else "neutral",
                materiality_score=score,
                uncertainties=("合同履行及实际影响尚未核实",),
            )
        )
    return changes


def _risk_changes(previous: dict[str, Any], current: dict[str, Any]) -> list[DetectedChange]:
    previous_records = {
        str(item.get("key")): item
        for item in _as_records(previous.get("records"))
        if item.get("key")
    }
    changes: list[DetectedChange] = []
    for item in _as_records(current.get("records")):
        key = str(item.get("key") or "")
        if not key:
            continue
        label = _text(item.get("label")) or "风险相关记录"
        current_count = _text(_as_dict(item.get("fields")).get("count"))
        previous_count = _text(_as_dict(previous_records.get(key, {}).get("fields")).get("count"))
        if current_count is None or current_count == previous_count:
            continue
        changes.append(
            DetectedChange(
                module_code="risk",
                change_key=f"risk:{key}:count",
                change_type="risk_record_count_changed",
                field_label=label,
                before_value=previous_count,
                after_value=current_count,
                title="待核实记录数量发生变化",
                summary=f"授权来源中“{label}”记录数量变为 {current_count}，具体责任与影响待核实。",
                event_type="legal_compliance",
                direction="unknown",
                materiality_score=65,
                risk_severity="none",
                uncertainties=("当前只确认来源记录数量变化，不构成风险责任或严重程度结论",),
                publishable=False,
            )
        )
    return changes


def _intellectual_property_changes(
    previous: dict[str, Any], current: dict[str, Any]
) -> list[DetectedChange]:
    previous_fields = _as_dict(previous.get("fields"))
    current_fields = _as_dict(current.get("fields"))
    labels = {
        "invention_grants": "发明授权数量",
        "invention_publications": "发明公布数量",
        "utility_models": "实用新型数量",
        "design_patents": "外观设计数量",
        "software_copyrights": "软件著作权数量",
    }
    changes: list[DetectedChange] = []
    for key, label in labels.items():
        before = _text(previous_fields.get(key))
        after = _text(current_fields.get(key))
        if before is None or after is None or before == after:
            continue
        changes.append(
            DetectedChange(
                module_code="intellectual_property",
                change_key=f"intellectual_property:{key}",
                change_type="intellectual_property_count_changed",
                field_label=label,
                before_value=before,
                after_value=after,
                title=f"{label}发生变化",
                summary=f"授权来源显示，{label}由 {before} 变为 {after}。",
                event_type="product_technology",
                direction="neutral",
                materiality_score=35,
                uncertainties=("数量变化不等同于技术质量或商业价值变化",),
            )
        )
    return changes


def detect_research_changes(
    previous_states: dict[str, object],
    current_states: dict[str, object],
) -> tuple[list[DetectedChange], list[DetectedChange]]:
    """Return material changes and low-value/archived changes.

    A first snapshot establishes the comparison baseline and deliberately produces no change.
    Missing modules are ignored so partial provider failures cannot be interpreted as deletions.
    """

    if not previous_states:
        return [], []
    detected: list[DetectedChange] = []
    for module_code, current_value in current_states.items():
        if module_code not in previous_states:
            continue
        previous = _as_dict(previous_states[module_code])
        current = _as_dict(current_value)
        if module_code == "company_base":
            detected.extend(_registration_changes(previous, current))
            detected.extend(_shareholder_changes(previous, current))
        elif module_code == "history":
            detected.extend(_history_changes(previous, current))
        elif module_code == "operation":
            detected.extend(_operation_changes(previous, current))
        elif module_code == "risk":
            detected.extend(_risk_changes(previous, current))
        elif module_code == "intellectual_property":
            detected.extend(_intellectual_property_changes(previous, current))

    material = [item for item in detected if item.materiality_score >= MATERIAL_CHANGE_MIN_SCORE]
    archived = [item for item in detected if item.materiality_score < MATERIAL_CHANGE_MIN_SCORE]
    return material, archived
