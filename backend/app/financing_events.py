"""仅从明确主体的融资披露提取有原文支持的字段。"""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date

from backend.app.research_subject import matched_name, normalize

SCHEMA_VERSION = "financing-disclosure-v1"
ROUND = r"(?:Pre[-－ ]?[A-F]|[A-F](?:\+|＋)?|天使|种子|战略)(?:轮)"
AMOUNT = (
    r"(?:(?:近|约|超过|超|逾|数|数十|数百|数千|上)?"
    r"(?:[0-9]+(?:\.[0-9]+)?|[一二两三四五六七八九十百千万亿]+)?"
    r"(?:千万元|百万元|万元|亿元|亿美元|万美元|亿港元|万港元|元人民币|人民币|美元|港元|元))"
)


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class FinancingCandidate:
    subject_name: str
    subject_scope: str
    round: str | None
    amount_text: str | None
    investors: tuple[str, ...]
    disclosed_on: str | None
    occurred_on: str | None
    evidence: str
    is_correction: bool
    issues: tuple[str, ...] = ()

    def fields(self) -> dict:
        return {
            key: value
            for key, value in asdict(self).items()
            if key not in {"evidence", "is_correction", "issues"}
        }

    def facts(self) -> list[dict]:
        fields = [
            ("披露主体", self.subject_name),
            ("融资轮次", self.round),
            ("公开融资金额（原口径）", self.amount_text),
            ("明确投资方", "、".join(self.investors) or None),
            ("披露日期", self.disclosed_on),
            ("明确发生日期", self.occurred_on),
        ]
        return [{"name": name, "value": value, "unit": None} for name, value in fields if value]


def round_value(text: str) -> str | None:
    values = {
        re.sub(r"[ －-]", "", m.group()).upper().replace("＋", "+")
        for m in re.finditer(ROUND, text, re.I)
    }
    return next(iter(values)) if len(values) == 1 else None


def amount_value(text: str) -> str | None:
    values = {
        m.group()
        for m in re.finditer(AMOUNT, text)
        if len(m.group()) > 2 and not m.group().startswith(("美元", "人民币"))
    }
    return next(iter(values)) if len(values) == 1 else None


def extract_financing(company, body: str, published_on: date | None) -> FinancingCandidate | None:
    sentences = [s.strip() for s in re.split(r"(?<=[。！？!?；;])|\n+", body) if s.strip()]
    found = []
    for index, sentence in enumerate(sentences):
        name = matched_name(company, sentence)
        if not name:
            continue
        offset = sentence.find(name)
        if offset < 0:
            continue
        predicate = sentence[offset + len(name) :]
        # 主体必须是被融资方；不把其子公司、投资行为或计划当成本次融资。
        if re.match(r"(?:旗下|的)?(?:子公司|母公司|集团)", predicate) or re.search(
            r"拟|计划|否认|未完成|尚未", predicate[:60]
        ):
            continue
        if not re.match(
            r"[，,\s]*(?:(?:近日|日前|今日|宣布|已|正式|成功|于\d{4}年\d{1,2}月\d{1,2}日)"
            r"[，,\s]*)*(?:完成|获得|获|宣布完成|宣布获得)",
            predicate,
        ):
            continue
        if "融资" not in predicate[:160]:
            continue
        support = sentence
        for following in sentences[index + 1 : index + 3]:
            if not re.match(r"(?:本轮|此次|该轮)(?:融资|投资)?", following):
                break
            support += following
        found.append((name, support))
    if not found:
        return None
    name, support = found[0]
    support = support[:1000]
    round_text = round_value(support)
    # 金额仅取融资句；下一句估值、营收、历史累计额不参与抽取。
    amount = amount_value(re.split(r"[。；;]", support)[0])
    investors = []
    for match in re.finditer(r"由([^。；;]{1,100}?)(?:领投|联合领投|跟投|参与投资)", support):
        investors.extend(v.strip() for v in re.split(r"、|及|和|与", match.group(1)) if v.strip())
    for match in re.finditer(r"(?:，|,)([^，,。；;]{2,40})(?:跟投|参与投资)", support):
        investors.extend(v.strip() for v in match.group(1).split("、") if v.strip())
    disclosure = published_on.isoformat() if published_on else None
    occurred = None
    date_match = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日[，,\s]+", support)
    if date_match:
        try:
            day = date(*map(int, date_match.groups())).isoformat()
            disclosure = day
            tail = support[date_match.end() :]
            if tail.startswith(name) and re.match(
                r"[，,\s]*(?:已|正式|成功)?完成", tail[len(name) :]
            ):
                occurred = day
        except ValueError:
            pass
    scope = (
        f"legal_entity:{company.credit_code}"
        if name == company.legal_name or name in getattr(company, "legal_aliases", ())
        else f"brand:{normalize(name)}"
    )
    issues = []
    if len(found) > 1 or len(list(re.finditer(ROUND, support, re.I))) > 1:
        issues.append("multiple_financing_mentions")
    if not round_text:
        issues.append("round_unknown")
    if not disclosure:
        issues.append("disclosure_date_unknown")
    return FinancingCandidate(
        name,
        scope,
        round_text,
        amount,
        tuple(sorted(set(investors))),
        disclosure,
        occurred,
        support[:1000],
        bool(re.search(r"更正|纠正|修正", body)),
        tuple(issues),
    )


def same_matter(left: FinancingCandidate, right: FinancingCandidate) -> bool:
    return (
        not left.issues
        and not right.issues
        and left.subject_scope == right.subject_scope
        and left.round == right.round
        and left.disclosed_on == right.disclosed_on
    )


def financing_observations(evidence_rows, visible_ids) -> list[dict]:
    from backend.app.schemas import FinancingObservationOut

    result = []
    for evidence in evidence_rows:
        payload = evidence.display_detail_payload or {}
        if (
            evidence.id not in visible_ids
            or payload.get("schema_version") != SCHEMA_VERSION
            or not evidence.display_allowed
            or evidence.display_license_status not in {"public", "permission_confirmed"}
            or evidence.display_url_health_status != "healthy"
        ):
            continue
        item = payload.get("financing_observation")
        if isinstance(item, dict):
            try:
                result.append(
                    FinancingObservationOut.model_validate({**item, "evidence_id": evidence.id})
                )
            except ValueError:
                continue
    return sorted(result, key=lambda item: item.observed_at, reverse=True)
