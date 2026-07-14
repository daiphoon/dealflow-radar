from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from backend.app.event_schema import (
    Direction,
    EventType,
    EvidenceSpan,
    Fact,
    PublishDecision,
    RiskSeverity,
    SourceQuality,
    StrictEventCandidate,
)


def candidate_values() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "prompt_version": "demo-v1",
        "matched_company_id": uuid4(),
        "company_match_confidence": 1.0,
        "event_type": EventType.PRODUCT_TECHNOLOGY,
        "event_subtype": "technology_milestone",
        "occurred_at": datetime(2026, 6, 1, tzinfo=UTC),
        "published_at": datetime(2026, 6, 1, tzinfo=UTC),
        "direction": Direction.POSITIVE,
        "materiality_score": 60,
        "risk_severity": RiskSeverity.LOW,
        "confidence_score": 0.95,
        "source_quality": SourceQuality.A,
        "title": "示例事件",
        "summary": "示例证据支持的事实摘要。",
        "facts": [Fact(name="milestone", value="完成示例里程碑")],
        "uncertainties": ["商业化时间未知"],
        "amount": Decimal("100.00"),
        "currency": "CNY",
        "counterparty": None,
        "location": None,
        "evidence_spans": [EvidenceSpan(document_id=uuid4(), quote="示例证据")],
        "publish_decision": PublishDecision.HUMAN_REVIEW,
        "requires_human_review": True,
    }


def test_valid_candidate_preserves_separate_scores() -> None:
    candidate = StrictEventCandidate.model_validate(candidate_values())

    assert candidate.materiality_score == 60
    assert candidate.risk_severity is RiskSeverity.LOW
    assert candidate.confidence_score == 0.95
    assert candidate.source_quality is SourceQuality.A


def test_amount_requires_currency() -> None:
    values = candidate_values()
    values["currency"] = None

    with pytest.raises(ValidationError, match="amount and currency"):
        StrictEventCandidate.model_validate(values)


def test_high_risk_event_cannot_auto_publish() -> None:
    values = candidate_values()
    values.update(
        risk_severity=RiskSeverity.HIGH,
        publish_decision=PublishDecision.AUTO_PUBLISH,
        requires_human_review=False,
    )

    with pytest.raises(ValidationError, match="high-risk event"):
        StrictEventCandidate.model_validate(values)


def test_unresolved_company_cannot_auto_publish() -> None:
    values = candidate_values()
    values.update(
        matched_company_id=None,
        publish_decision=PublishDecision.AUTO_PUBLISH,
        requires_human_review=False,
    )

    with pytest.raises(ValidationError, match="resolved company"):
        StrictEventCandidate.model_validate(values)


def test_evidence_offsets_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="end_offset"):
        EvidenceSpan(
            document_id=UUID("00000000-0000-0000-0000-000000000001"),
            quote="示例证据",
            start_offset=10,
            end_offset=2,
        )
