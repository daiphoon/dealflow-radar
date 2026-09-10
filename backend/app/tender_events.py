"""单公告带标签正文的离线候选抽取；不访问数据库、网络或发布入口。"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from backend.app.event_schema import EventType, EvidenceSpan


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class TenderSubject(_StrictModel):
    # 由既有主体解析提供；本模块不能授予公司核验状态。
    company_id: UUID
    legal_name: str = Field(min_length=1, max_length=240)
    credit_code: str | None = Field(default=None, pattern=r"^[0-9A-Z]{18}$")
    identity_status: Literal["verified", "unresolved"]


class TenderDocumentReference(_StrictModel):
    document_id: UUID
    source_id: UUID
    canonical_url: str = Field(min_length=1, max_length=1000)
    title: str = Field(min_length=1, max_length=500)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_at: AwareDatetime | None
    observed_at: AwareDatetime
    license_status: str = Field(min_length=1, max_length=64)

    @field_validator("canonical_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("document URL must be HTTP(S) without credentials")
        return value


class TenderDocument(TenderDocumentReference):
    # 上游已解析的单公告正文；不是让抽取器重新下载或解析网页。
    body: str = Field(min_length=1, max_length=100_000)


class TenderFieldEvidence(_StrictModel):
    field: str
    span: EvidenceSpan


def _digest(values: list[object]) -> str:
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class TenderCandidate(_StrictModel):
    schema_version: Literal["tender-candidate-v1"] = "tender-candidate-v1"
    fingerprint_version: Literal["tender-v1"] = "tender-v1"
    event_type: Literal[EventType.CONTRACT_COMMERCIAL] = EventType.CONTRACT_COMMERCIAL
    status: Literal["candidate"] = "candidate"
    publication_route: Literal["unconfirmed_lead"] = "unconfirmed_lead"
    subject: TenderSubject
    document: TenderDocumentReference
    evidence_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    phase: Literal["award_result", "award_candidate"]
    is_correction: bool
    notice_number: str | None
    original_notice_number: str | None
    buyer: str | None
    project_number: str | None
    project_name: str
    lot_number: str | None
    supplier_name: str
    supplier_credit_code: str
    occurred_on: date | None
    amount: Decimal | None = Field(ge=0)
    currency: Literal["CNY"] | None
    reported_date: str | None
    reported_amount: str | None
    evidence: tuple[TenderFieldEvidence, ...] = Field(min_length=1, max_length=20)
    issues: tuple[str, ...]

    @model_validator(mode="after")
    def validate_candidate(self) -> TenderCandidate:
        if (
            self.subject.identity_status != "verified"
            or self.supplier_name != _normalize(self.subject.legal_name)
            or self.supplier_credit_code != self.subject.credit_code
        ):
            raise ValueError("candidate requires a resolved matching subject")
        if (self.amount is None) != (self.currency is None):
            raise ValueError("amount and currency must be provided together")
        if any(
            item.span.document_id != self.document.document_id
            or item.span.start_offset is None
            or item.span.end_offset is None
            for item in self.evidence
        ):
            raise ValueError("field evidence requires this document and exact offsets")
        return self

    @computed_field
    @property
    def date_precision(self) -> Literal["day", "unknown"]:
        return "day" if self.occurred_on is not None else "unknown"

    @computed_field
    @property
    def business_key(self) -> str | None:
        anchor = self.original_notice_number if self.is_correction else self.notice_number
        if not all((anchor, self.buyer, self.project_number, self.lot_number)):
            return None
        # 公告编号防止同项目/标段重复采购被误合并；更正指向原公告。
        return _digest(
            [
                self.fingerprint_version,
                str(self.subject.company_id),
                self.buyer,
                self.project_number,
                self.lot_number,
                self.phase,
                anchor,
            ]
        )

    @computed_field
    @property
    def fact_version(self) -> str:
        # 只标识已解析的业务字段；原文变化和未解析字段仍保存在各自证据中。
        amount_text = format(self.amount, "f") if self.amount is not None else None
        if amount_text is not None and "." in amount_text:
            amount_text = amount_text.rstrip("0").rstrip(".")
        return _digest(
            [
                self.schema_version,
                str(self.subject.company_id),
                self.buyer,
                self.project_number,
                self.project_name,
                self.lot_number,
                self.phase,
                self.supplier_name,
                self.supplier_credit_code,
                self.occurred_on.isoformat() if self.occurred_on else None,
                amount_text,
                self.currency,
            ]
        )


@dataclass(frozen=True)
class TenderExtractionResult:
    status: Literal["candidate", "identity_unresolved", "unsupported"]
    issues: tuple[str, ...]
    candidate: TenderCandidate | None = None


@dataclass(frozen=True)
class _Field:
    value: str
    span: EvidenceSpan


_LABELS = {
    "公告类型": "notice_type",
    "公告编号": "notice_number",
    "原公告编号": "original_notice_number",
    "采购人": "buyer",
    "招标人": "buyer",
    "项目编号": "project_number",
    "项目名称": "project_name",
    "标段编号": "lot_number",
    "包号": "lot_number",
    "中标人": "awarded_supplier",
    "中标人统一社会信用代码": "awarded_credit_code",
    "中标日期": "awarded_on",
    "中标金额": "awarded_amount",
    "中标候选人": "candidate_supplier",
    "中标候选人统一社会信用代码": "candidate_credit_code",
    "公示日期": "candidate_on",
    "投标报价": "candidate_amount",
}
_NOTICE_TYPES = {
    "中标结果公告": ("award_result", False),
    "中标公告": ("award_result", False),
    "中标候选人公示": ("award_candidate", False),
    "中标结果更正公告": ("award_result", True),
    "中标候选人更正公示": ("award_candidate", True),
}
_UNKNOWN_VALUES = {"", "未知", "未披露", "不详", "待确认", "-"}
_NUMBER = r"(?:[0-9]{1,18}|[0-9]{1,3}(?:,[0-9]{3}){1,5})(?:\.[0-9]{1,6})?"


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _fields(document: TenderDocument) -> dict[str, _Field] | None:
    fields: dict[str, _Field] = {}
    for match in re.finditer(r"[^\r\n]+", document.body):
        line = match.group()
        key, separator, value = _normalize(line).partition(":")
        name = _LABELS.get(key.strip())
        if not separator or name is None:
            continue
        # 多公告/多标段或重复标签需要上游分段，不能擅自取第一项。
        if name in fields or len(line) > 1000:
            return None
        fields[name] = _Field(
            value=value.strip(),
            span=EvidenceSpan(
                document_id=document.document_id,
                quote=line,
                start_offset=match.start(),
                end_offset=match.end(),
            ),
        )
    return fields


def _value(fields: dict[str, _Field], name: str) -> str | None:
    field = fields.get(name)
    return field.value if field is not None and field.value not in _UNKNOWN_VALUES else None


def _occurred_on(value: str | None) -> tuple[date | None, str | None]:
    if value is None:
        return None, "event_date_unknown"
    match = re.fullmatch(r"([0-9]{4})年([0-9]{1,2})月([0-9]{1,2})日", value)
    if match is None:
        match = re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", value)
    if match is None:
        return None, "event_date_unknown"
    try:
        return date(*map(int, match.groups())), None
    except ValueError:
        return None, "event_date_invalid"


def _amount(value: str | None) -> tuple[Decimal | None, str | None]:
    if value is None:
        return None, "amount_unknown"
    match = re.fullmatch(rf"(?:人民币|CNY)\s*({_NUMBER})\s*(元|万元|亿元)", value)
    if match is None:
        match = re.fullmatch(rf"({_NUMBER})\s*(元|万元|亿元)\s*(?:人民币|CNY)", value)
    if match is None:
        return None, "amount_unsupported"
    factors = {"元": Decimal(1), "万元": Decimal(10_000), "亿元": Decimal(100_000_000)}
    with localcontext() as context:
        context.prec = 40  # 覆盖受限输入和单位换算，不受调用者精度设置影响。
        return Decimal(match[1].replace(",", "")) * factors[match[2]], None


def extract_tender_candidate(
    document: TenderDocument, subject: TenderSubject
) -> TenderExtractionResult:
    """只返回候选及字段证据；调用者仍须核对底稿权限、许可和发布条件。"""
    if subject.identity_status != "verified":
        return TenderExtractionResult("identity_unresolved", ("subject_unresolved",))
    fields = _fields(document)
    if fields is None:
        return TenderExtractionResult("unsupported", ("ambiguous_fields",))
    notice_type = _NOTICE_TYPES.get(_value(fields, "notice_type"))
    if notice_type is None:
        return TenderExtractionResult("unsupported", ("unsupported_notice_type",))
    phase, is_correction = notice_type
    prefix = "awarded" if phase == "award_result" else "candidate"
    other_prefix = "candidate" if phase == "award_result" else "awarded"
    if any(key.startswith(f"{other_prefix}_") for key in fields):
        return TenderExtractionResult("unsupported", ("phase_field_conflict",))
    supplier_name = _value(fields, f"{prefix}_supplier")
    supplier_code = _value(fields, f"{prefix}_credit_code")
    if supplier_name != _normalize(subject.legal_name):
        return TenderExtractionResult("identity_unresolved", ("subject_name_mismatch",))
    # 首类仅接受名称和代码共同配对的记录；无代码不把同名视为唯一主体。
    if supplier_code is None or subject.credit_code is None:
        return TenderExtractionResult("identity_unresolved", ("subject_code_missing",))
    if supplier_code != subject.credit_code:
        return TenderExtractionResult("identity_unresolved", ("subject_code_mismatch",))
    project_name = _value(fields, "project_name")
    if project_name is None:
        return TenderExtractionResult("unsupported", ("project_name_missing",))
    reported_date = _value(fields, f"{prefix}_on")
    reported_amount = _value(fields, f"{prefix}_amount")
    occurred_on, date_issue = _occurred_on(reported_date)
    amount, amount_issue = _amount(reported_amount)
    observed_on = document.observed_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
    if occurred_on is not None and occurred_on > observed_on:
        occurred_on, date_issue = None, "event_date_in_future"
    notice_number = _value(fields, "notice_number")
    original_notice_number = _value(fields, "original_notice_number")
    if not is_correction and original_notice_number is not None:
        return TenderExtractionResult("unsupported", ("correction_type_required",))
    if is_correction and notice_number == original_notice_number and notice_number is not None:
        return TenderExtractionResult("unsupported", ("correction_self_reference",))
    identity = {
        "buyer": _value(fields, "buyer"),
        "project_number": _value(fields, "project_number"),
        "lot_number": _value(fields, "lot_number"),
    }
    anchor = original_notice_number if is_correction else notice_number
    issues = tuple(
        issue
        for issue in (
            date_issue,
            amount_issue,
            "business_identity_incomplete" if not anchor or not all(identity.values()) else None,
        )
        if issue is not None
    )
    candidate = TenderCandidate(
        subject=subject,
        document=TenderDocumentReference.model_validate(document.model_dump(exclude={"body"})),
        evidence_text_hash=hashlib.sha256(document.body.encode()).hexdigest(),
        phase=phase,
        is_correction=is_correction,
        notice_number=notice_number,
        original_notice_number=original_notice_number,
        **identity,
        project_name=project_name,
        supplier_name=supplier_name,
        supplier_credit_code=supplier_code,
        occurred_on=occurred_on,
        amount=amount,
        currency="CNY" if amount is not None else None,
        reported_date=reported_date,
        reported_amount=reported_amount,
        evidence=tuple(
            TenderFieldEvidence(field=name, span=field.span)
            for name, field in sorted(fields.items())
        ),
        issues=issues,
    )
    return TenderExtractionResult("candidate", issues, candidate)


def tender_candidate_facts(candidate: TenderCandidate) -> list[tuple[str, str, str, str | None]]:
    prefix = "awarded" if candidate.phase == "award_result" else "candidate"
    result = [
        ("project_name", "项目名称", candidate.project_name, None),
        ("project_number", "项目编号", candidate.project_number, None),
        ("buyer", "采购人", candidate.buyer, None),
        ("lot_number", "标段编号", candidate.lot_number, None),
        (
            "notice_type",
            "公告阶段",
            "中标结果" if candidate.phase == "award_result" else "中标候选人",
            None,
        ),
        (f"{prefix}_supplier", "供应商全称", candidate.supplier_name, None),
        (f"{prefix}_credit_code", "供应商信用代码", candidate.supplier_credit_code, None),
    ]
    if candidate.occurred_on is not None:
        result.append(
            (
                f"{prefix}_on",
                "中标日期" if prefix == "awarded" else "公示日期",
                candidate.occurred_on.isoformat(),
                None,
            )
        )
    if candidate.amount is not None:
        amount = format(candidate.amount, "f")
        if "." in amount:
            amount = amount.rstrip("0").rstrip(".")
        result.append(
            (
                f"{prefix}_amount",
                "中标金额" if prefix == "awarded" else "投标报价",
                amount,
                "元（人民币）",
            )
        )
    return [item for item in result if item[2] is not None]
