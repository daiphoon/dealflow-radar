"""片段使用原始 UTF-8 字节；对象指纹保留独立、版本明确的编码契约。"""

import hashlib
import json

EXCERPT_HASH_VERSION = "sha256-utf8-exact-v1"
OBJECT_HASH_VERSION = "sha256-json-sorted-v1"


def hash_excerpt_bytes(value: str) -> str:
    # 不 strip、不归一化换行或 Unicode；偏移以原始 Python 字符串索引表达。
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_canonical_object(value) -> str:
    return hash_excerpt_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True))


def excerpt_hash_status(evidence, source_text=None):
    payload = evidence.display_detail_payload or {}
    version = payload.get("excerpt_hash_version")
    excerpt = evidence.evidence_excerpt
    if version not in (None, EXCERPT_HASH_VERSION):
        return "unknown_hash_version"
    if not excerpt:
        return "empty_excerpt"
    if source_text is not None and excerpt not in source_text:
        return "excerpt_outside_document"
    if evidence.span_hash == hash_excerpt_bytes(excerpt):
        return "exact"
    # 只接受旧 matter 写入格式；标明新算法后绝不回退旧算法。
    old = payload.get("matter_observation", {})
    if (
        version is None
        and payload.get("schema_version") == "matter-v1"
        and old.get("excerpt") == excerpt
        and old.get("fact_version")
        == hash_canonical_object(
            {
                "category": old.get("category"),
                "subtype": old.get("subtype"),
                "subject": old.get("subject"),
                "scope": old.get("scope"),
                "action": old.get("excerpt"),
                "status": old.get("status"),
                "fields": old.get("fields"),
                "issues": old.get("issues"),
            }
        )
        and evidence.span_hash == hash_canonical_object(excerpt)
    ):
        return "legacy_matter_json_string"
    return "hash_mismatch"


def evidence_signature(evidence):
    return hash_canonical_object(
        {
            "excerpt": evidence.evidence_excerpt,
            "span_hash": evidence.span_hash,
            "source": str(evidence.source_event_evidence_id),
            "support_type": evidence.support_type,
            "detail": evidence.display_detail_payload,
        }
    )


def usable_matter_evidence(session, evidence):
    """读取时重新核对共享引文链与展示许可，不复活已撤回的上游引用。"""
    from backend.app.models import Event, EventEvidence

    current = evidence
    visited = set()
    for _ in range(8):
        if current.id in visited:
            return False
        visited.add(current.id)
        if (
            not current.display_allowed
            or current.display_license_status != "public"
            or current.display_url_health_status != "healthy"
        ):
            return False
        parent_event = session.get(Event, current.event_id)
        if parent_event is None or parent_event.status in {"retracted", "rejected"}:
            return False
        if current.source_event_evidence_id is None:
            return excerpt_hash_status(current) in {"exact", "legacy_matter_json_string"}
        source = session.get(EventEvidence, current.source_event_evidence_id)
        if (
            source is None
            or current.span_hash != source.span_hash
            or not source.evidence_excerpt.startswith(current.evidence_excerpt)
        ):
            return False
        current = source
    return False
