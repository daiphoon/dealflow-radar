from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.evidence_integrity import evidence_signature, excerpt_hash_status
from backend.app.models import (
    EntityMention,
    Event,
    EventEvidence,
    EventFact,
    EventFactSupport,
    RawDocument,
    utc_now,
)

SUPPORT_POLICY_VERSION = "evidence-fact-support-v1"
SUPPORT_STATUSES = {
    "supported",
    "partial",
    "conflicting",
    "pending_review",
    "unsupported",
}

_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9])[-+]?\d[\d,]*(?:\.\d+)?%?")
_DATE_PATTERN = re.compile(
    r"(?P<year>(?:19|20)\d{2})[年./-]"
    r"(?P<month>0?[1-9]|1[0-2])[月./-]"
    r"(?P<day>0?[1-9]|[12]\d|3[01])日?"
)


@dataclass(frozen=True)
class _EvidenceContext:
    excerpt: str
    title: str
    citation_complete: bool
    subject_status: str
    source_date: date | None
    locator: dict[str, object]


@dataclass(frozen=True)
class _Assessment:
    status: str
    locator: dict[str, object]
    checks: dict[str, object]
    reasons: list[str]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _canonical_fact_component(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _fact_parts(value: object) -> tuple[str, str, str | None] | None:
    if not isinstance(value, dict):
        return None
    name = str(value.get("name") or "").strip()
    fact_value = str(value.get("value") or "").strip()
    unit_value = value.get("unit")
    unit = str(unit_value).strip() if unit_value is not None else None
    if not name or not fact_value:
        return None
    return name, fact_value, unit or None


def fact_key(name: str, value: str, unit: str | None) -> str:
    canonical = json.dumps(
        {
            "name": _canonical_fact_component(name),
            "unit": _canonical_fact_component(unit) if unit is not None else None,
            "value": _canonical_fact_component(value),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256(canonical)


def _number_tokens(value: str) -> set[str]:
    return {match.group(0).replace(",", "") for match in _NUMBER_PATTERN.finditer(value)}


def _date_tokens(value: str) -> set[str]:
    return {
        f"{int(match.group('year')):04d}-{int(match.group('month')):02d}-"
        f"{int(match.group('day')):02d}"
        for match in _DATE_PATTERN.finditer(value)
    }


def _source_evidence(session: Session, evidence: EventEvidence) -> EventEvidence | None:
    current = evidence
    visited = {current.id}
    for _ in range(8):
        if current.source_event_evidence_id is None:
            return current
        source = session.get(EventEvidence, current.source_event_evidence_id)
        if source is None or source.id in visited:
            return None
        if (
            not source.evidence_excerpt.startswith(current.evidence_excerpt)
            or current.span_hash != source.span_hash
        ):
            return None
        visited.add(source.id)
        current = source
    return None


def _source_date(evidence: EventEvidence, document: RawDocument | None) -> date | None:
    if evidence.display_published_on is not None:
        return evidence.display_published_on
    if evidence.display_published_at is not None:
        return evidence.display_published_at.date()
    if document is None:
        return None
    if document.published_on is not None:
        return document.published_on
    return document.published_at.date() if document.published_at is not None else None


def _evidence_context(
    session: Session,
    event: Event,
    evidence: EventEvidence,
) -> _EvidenceContext:
    origin = _source_evidence(session, evidence)
    document = (
        session.get(RawDocument, origin.raw_document_id)
        if origin is not None and origin.raw_document_id is not None
        else None
    )
    excerpt = evidence.evidence_excerpt
    title = (evidence.display_title or (document.title if document is not None else "")).strip()

    if evidence.source_event_evidence_id is not None:
        source_text = (document.payload or {}).get("excerpt") if document is not None else None
        origin_hash_valid = origin is not None and excerpt_hash_status(
            origin, source_text if isinstance(source_text, str) else None
        ) in {"exact", "legacy_matter_json_string"}
        if origin is None:
            return _EvidenceContext(
                excerpt, title, False, "unknown", None, {"kind": "invalid_reference_chain"}
            )
        display_is_bounded_excerpt = bool(excerpt) and origin.evidence_excerpt.startswith(excerpt)
        citation_complete = (
            origin_hash_valid
            and display_is_bounded_excerpt
            and evidence.span_hash == origin.span_hash
        )
        locator: dict[str, object] = {
            "kind": "shared_excerpt",
            "display_excerpt_hash": _sha256(excerpt) if excerpt else None,
            "origin_span_hash": origin.span_hash,
        }
    else:
        source_text = (document.payload or {}).get("excerpt") if document is not None else None
        hash_status = excerpt_hash_status(
            evidence, source_text if isinstance(source_text, str) else None
        )
        citation_complete = hash_status in {"exact", "legacy_matter_json_string"}
        locator = {
            "kind": "excerpt",
            "span_hash": evidence.span_hash,
            "source_content_hash": document.content_hash if document else None,
            "source_excerpt_start": source_text.find(excerpt)
            if isinstance(source_text, str)
            else None,
            "source_excerpt_end": source_text.find(excerpt) + len(excerpt)
            if isinstance(source_text, str) and excerpt in source_text
            else None,
            "source_windows": document.payload.get("source_windows", []) if document else [],
            "coordinate_space": "stored_excerpt",
        }

    subject_status = "unknown"
    if document is not None:
        mentions = list(
            session.scalars(
                select(EntityMention).where(
                    EntityMention.raw_document_id == document.id,
                    EntityMention.resolution_status == "verified",
                    EntityMention.candidate_company_id.is_not(None),
                )
            )
        )
        matching_mentions = [
            mention for mention in mentions if mention.candidate_company_id == event.company_id
        ]
        corpus = _normalized(f"{title}\n{excerpt}")
        if any(_normalized(mention.mention_text) in corpus for mention in matching_mentions):
            subject_status = "matched_locator"
        elif matching_mentions:
            subject_status = "matched_document"
        elif mentions:
            subject_status = "conflicting"

    return _EvidenceContext(
        excerpt=excerpt,
        title=title,
        citation_complete=citation_complete,
        subject_status=subject_status,
        source_date=_source_date(evidence, document),
        locator=locator,
    )


def _assess(
    session: Session,
    event: Event,
    fact: EventFact,
    evidence: EventEvidence,
    *,
    duplicate_name_conflict: bool,
) -> _Assessment:
    context = _evidence_context(session, event, evidence)
    corpus = f"{context.title}\n{context.excerpt}".strip()
    normalized_corpus = _normalized(corpus)
    normalized_value = _normalized(fact.value)
    normalized_name = _normalized(fact.name)
    exact_value_match = bool(normalized_value) and normalized_value in normalized_corpus
    name_match = bool(normalized_name) and normalized_name in normalized_corpus

    fact_numbers = _number_tokens(fact.value)
    evidence_numbers = _number_tokens(corpus)
    fact_dates = _date_tokens(fact.value)
    evidence_dates = _date_tokens(corpus)
    matching_numbers = fact_numbers & evidence_numbers
    matching_dates = fact_dates & evidence_dates
    event_source_date = event.published_on or (
        event.published_at.date() if event.published_at is not None else None
    )
    if event_source_date is None or context.source_date is None:
        source_date_check = "unknown"
    elif event_source_date == context.source_date:
        source_date_check = "matched"
    else:
        source_date_check = "different_source_date"

    locator = dict(context.locator)
    raw_offset = context.excerpt.find(fact.value)
    if raw_offset >= 0:
        locator.update(
            {
                "kind": "excerpt",
                "start": raw_offset,
                "end": raw_offset + len(fact.value),
            }
        )
    elif fact.value in context.title:
        title_offset = context.title.find(fact.value)
        locator.update(
            {
                "kind": "document_title",
                "start": title_offset,
                "end": title_offset + len(fact.value),
                "title_hash": _sha256(context.title),
            }
        )

    checks: dict[str, object] = {
        "citation_complete": context.citation_complete,
        "evidence_signature": evidence_signature(evidence),
        "subject_status": context.subject_status,
        "exact_value_match": exact_value_match,
        "name_match": name_match,
        "number_tokens_expected": sorted(fact_numbers),
        "number_tokens_matched": sorted(matching_numbers),
        "date_tokens_expected": sorted(fact_dates),
        "date_tokens_matched": sorted(matching_dates),
        "source_date_check": source_date_check,
        "duplicate_name_conflict": duplicate_name_conflict,
    }

    if not context.citation_complete:
        return _Assessment(
            "unsupported",
            locator,
            checks,
            ["evidence_locator_integrity_failed"],
        )
    if context.subject_status == "conflicting":
        return _Assessment(
            "conflicting",
            locator,
            checks,
            ["evidence_points_to_different_company"],
        )
    from backend.app.matter_validation import assess_matter_fact

    typed = assess_matter_fact(fact, evidence, context)
    if typed is not None:
        status, typed_checks, reasons = typed
        checks.update(typed_checks)
        return _Assessment(status, locator, checks, reasons)
    if duplicate_name_conflict:
        return _Assessment(
            "conflicting",
            locator,
            checks,
            ["same_fact_name_has_different_values"],
        )
    if exact_value_match:
        if not name_match:
            return _Assessment(
                "pending_review",
                locator,
                checks,
                ["subject_and_value_present_but_fact_relation_not_proven"],
            )
        if context.subject_status == "matched_locator":
            return _Assessment(
                "supported",
                locator,
                checks,
                ["subject_and_value_supported_by_same_evidence"],
            )
        if context.subject_status == "matched_document":
            return _Assessment(
                "partial",
                locator,
                checks,
                ["value_supported_but_subject_not_in_same_locator"],
            )
        return _Assessment(
            "pending_review",
            locator,
            checks,
            ["value_present_but_company_identity_not_proven_in_evidence"],
        )

    expected_tokens = fact_numbers | fact_dates
    matched_tokens = matching_numbers | matching_dates
    if expected_tokens and matched_tokens:
        return _Assessment(
            "partial",
            locator,
            checks,
            ["only_part_of_numeric_or_date_value_is_supported"],
        )
    if expected_tokens and name_match and (evidence_numbers or evidence_dates):
        return _Assessment(
            "conflicting",
            locator,
            checks,
            ["same_fact_name_has_different_numeric_or_date_value"],
        )
    if name_match:
        return _Assessment(
            "partial",
            locator,
            checks,
            ["fact_name_present_but_value_not_fully_supported"],
        )
    return _Assessment(
        "unsupported",
        locator,
        checks,
        ["fact_value_not_found_in_evidence"],
    )


def assess_legacy_matter_support(session, event, fact, evidence):
    """兼容旧账本的只读计算；不修改旧哈希、支持行或原始事实。"""
    if excerpt_hash_status(evidence) != "legacy_matter_json_string":
        return None
    return _assess(session, event, fact, evidence, duplicate_name_conflict=False)


def materialize_event_fact_ledger(session: Session, event: Event) -> list[EventFact]:
    source_facts = list(event.facts or [])
    if event.fingerprint_version == "matter-v1":
        from backend.app.research_matters import FIELD_LABELS, LABELS, STATUS_LABELS

        for evidence in session.scalars(
            select(EventEvidence).where(EventEvidence.event_id == event.id)
        ):
            if not evidence.display_allowed:
                continue
            item = (evidence.display_detail_payload or {}).get("matter_observation", {})
            extra = [
                {"name": "事项阶段", "value": LABELS.get(item.get("subtype"), ""), "unit": None},
                {
                    "name": "动作状态",
                    "value": STATUS_LABELS.get(item.get("status"), ""),
                    "unit": None,
                },
            ]
            extra += [
                {"name": FIELD_LABELS.get(k, k), "value": v["value"], "unit": None}
                for k, v in item.get("fields", {}).items()
                if k != "date"
            ]
            source_facts.extend(v for v in extra if v["value"] and v not in source_facts)
    parsed = [parts for value in source_facts if (parts := _fact_parts(value)) is not None]
    if not parsed:
        return []
    keys = [fact_key(*parts) for parts in parsed]
    counts = Counter(keys)
    first_position: dict[str, int] = {}
    unique_parts: dict[str, tuple[str, str, str | None]] = {}
    name_to_keys: dict[str, set[str]] = defaultdict(set)
    for position, parts in enumerate(parsed):
        key = fact_key(*parts)
        first_position.setdefault(key, position)
        unique_parts.setdefault(key, parts)
        name_to_keys[_normalized(parts[0])].add(key)

    existing_facts = {
        fact.fact_key: fact
        for fact in session.scalars(select(EventFact).where(EventFact.event_id == event.id))
    }
    facts: list[EventFact] = []
    for key, parts in unique_parts.items():
        fact = existing_facts.get(key)
        if fact is None:
            fact = EventFact(
                id=uuid5(NAMESPACE_URL, f"dealflow-radar:event-fact:{event.id}:{key}"),
                event_id=event.id,
                fact_key=key,
                name=parts[0],
                value=parts[1],
                unit=parts[2],
                position=first_position[key],
                occurrence_count=counts[key],
            )
            session.add(fact)
        else:
            fact.position = first_position[key]
            fact.occurrence_count = counts[key]
        facts.append(fact)
    session.flush()

    evidence_rows = list(
        session.scalars(select(EventEvidence).where(EventEvidence.event_id == event.id))
    )
    existing_supports = {
        (support.event_fact_id, support.event_evidence_id): support
        for support in session.scalars(
            select(EventFactSupport).where(EventFactSupport.event_id == event.id)
        )
    }
    assessed_at = utc_now()
    for fact in facts:
        duplicate_name_conflict = len(name_to_keys[_normalized(fact.name)]) > 1
        for evidence in evidence_rows:
            assessment = _assess(
                session,
                event,
                fact,
                evidence,
                duplicate_name_conflict=duplicate_name_conflict,
            )
            support = existing_supports.get((fact.id, evidence.id))
            if support is None:
                support = EventFactSupport(
                    id=uuid5(
                        NAMESPACE_URL,
                        f"dealflow-radar:event-fact-support:{fact.id}:{evidence.id}",
                    ),
                    event_id=event.id,
                    event_fact_id=fact.id,
                    event_evidence_id=evidence.id,
                    support_status=assessment.status,
                    evidence_locator=assessment.locator,
                    deterministic_checks=assessment.checks,
                    support_reasons=assessment.reasons,
                    policy_version=SUPPORT_POLICY_VERSION,
                    assessed_at=assessed_at,
                )
                session.add(support)
            else:
                support.support_status = assessment.status
                support.evidence_locator = assessment.locator
                support.deterministic_checks = assessment.checks
                support.support_reasons = assessment.reasons
                support.policy_version = SUPPORT_POLICY_VERSION
                support.assessed_at = assessed_at
    session.flush()
    return sorted(facts, key=lambda fact: (fact.position, fact.fact_key))


def aggregate_support_status(statuses: list[str]) -> str:
    valid_statuses = {status for status in statuses if status in SUPPORT_STATUSES}
    if "conflicting" in valid_statuses:
        return "conflicting"
    if "supported" in valid_statuses:
        return "supported"
    if "partial" in valid_statuses:
        return "partial"
    if "pending_review" in valid_statuses:
        return "pending_review"
    return "unsupported"
