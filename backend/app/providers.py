from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from backend.app.event_schema import Direction, EventType, RiskSeverity, SourceQuality


class MockFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: str
    unit: str | None = None


class MockResearchRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_record_id: str
    company_legal_name: str
    company_alias: str
    registered_region: str
    title: str
    canonical_url: str
    published_at: datetime
    evidence_excerpt: str
    event_type: EventType
    event_subtype: str
    direction: Direction
    materiality_score: int = Field(ge=0, le=100)
    risk_severity: RiskSeverity
    confidence_score: float = Field(ge=0, le=1)
    source_quality: SourceQuality
    facts: list[MockFact] = Field(min_length=1)
    uncertainties: list[str] = Field(default_factory=list)
    requires_human_review: bool = True


class MockResearchProvider:
    code = "mock_research"
    external_calls = 0
    estimated_cost = 0

    def __init__(self, fixture_path: Path | None = None) -> None:
        self.fixture_path = fixture_path or (
            Path(__file__).resolve().parents[2] / "data" / "sample" / "mock_research.json"
        )
        self.load_count = 0

    def load(self) -> list[MockResearchRecord]:
        self.load_count += 1
        payload = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        return [MockResearchRecord.model_validate(item) for item in payload]
