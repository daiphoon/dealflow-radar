"""报告正文许可；外链许可和事实是否过时分别由既有投影处理。"""

from backend.app.models import PLATFORM_SHARED_SCOPE, RawDocument, Source


def _shared(row):
    return (
        row is not None
        and row.visibility_scope == PLATFORM_SHARED_SCOPE
        and row.owner_user_id is None
        and row.owner_tenant_id is None
    )


def _document_source(session, evidence):
    document = (
        session.get(RawDocument, evidence.raw_document_id) if evidence.raw_document_id else None
    )
    source = session.get(Source, document.source_id) if document else None
    return document, source


def report_evidence_is_demo(session, evidence):
    document, source = _document_source(session, evidence)
    return (
        _shared(evidence)
        and _shared(document)
        and source is not None
        and document.license_status == source.license_status == "synthetic_demo"
        and evidence.display_license_status in {None, "synthetic_demo"}
    )


def report_evidence_permission(session, evidence, *, withdrawn_fact=False):
    if not _shared(evidence):
        return False
    # 空展示许可仅兼容已有原文链，不等于新的展示授权。
    legacy = evidence.display_license_status is None and not evidence.display_allowed
    if legacy:
        document, source = _document_source(session, evidence)
        if not _shared(document) or source is None:
            return False
        return (
            document.license_status in {"public", "permission_confirmed"}
            and source.license_status in {"public", "permission_confirmed"}
        ) or report_evidence_is_demo(session, evidence)
    if not evidence.display_allowed and not withdrawn_fact:
        return False
    if not all(
        (
            evidence.display_source_name,
            evidence.display_source_quality,
            evidence.display_title,
            evidence.display_canonical_url,
            evidence.display_observed_at,
        )
    ):
        return False
    if evidence.display_license_status == "synthetic_demo":
        from backend.app.services import _evidence_detail_schema

        # 演示标记不能自行授予许可；必须同时有合法共享的既有来源链及有效展示数据。
        return (
            report_evidence_is_demo(session, evidence)
            and _evidence_detail_schema(evidence.display_detail_payload, "synthetic_demo")
            == "demo-evidence-v1"
        )
    return evidence.display_license_status in {"public", "permission_confirmed"}
