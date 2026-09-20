"""复用单公司事务批量接收资料，失败定点隔离，重跑复用已完成公司。"""

import re
from dataclasses import replace
from decimal import Decimal

from sqlalchemy import select, text

from backend.app.curated_import import (
    _require_curator,
    apply_curated_import,
    preview_curated_import,
)
from backend.app.curated_workbook import digest
from backend.app.database import set_request_context
from backend.app.models import UsageLedger, User
from backend.app.services import ImportConflictError


def company_selection(loaded, key, dataset_keys=None):
    return replace(
        loaded,
        dataset_key=(dataset_keys or {}).get(key, loaded.dataset_key),
        company_keys=(key,),
        companies=tuple(c for c in loaded.companies if c.key == key),
        records=tuple(r for r in loaded.records if r.company_key == key),
        issues=tuple(i for i in loaded.issues if i.company_key == key),
        notes=tuple(n for n in loaded.notes if not n["company_keys"] or key in n["company_keys"]),
    )


def preview_curated_batch(session, user, loaded, *, confirmed_at, reason, dataset_keys=None):
    _require_curator(session, user)
    dataset_keys = dataset_keys or {}
    if set(dataset_keys) - {c.key for c in loaded.companies} or any(
        not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value)
        for value in dataset_keys.values()
    ):
        raise ImportConflictError(
            "dataset mapping must contain selected companies and nonempty keys"
        )
    plans = [
        {
            "company_key": company.key,
            "preview": preview_curated_import(
                session,
                user,
                company_selection(loaded, company.key, dataset_keys),
                confirmed_at=confirmed_at,
                reason=reason,
            ),
        }
        for company in loaded.companies
    ]
    result = {
        "status": "dry_run",
        "dataset_keys": dataset_keys,
        "file_hash": loaded.file_hash,
        "companies": plans,
        "company_count": len(plans),
        "record_count": len(loaded.records),
        "notes": list(loaded.notes),
        "issues": [
            {"sheet": i.sheet, "row": i.row, "company_key": i.company_key, "message": i.message}
            for i in loaded.issues
        ],
        "database_writes": 0,
        "external_calls": 0,
    }
    result["preview_hash"] = digest(result)
    return result


def apply_curated_batch(
    session, user, loaded, *, confirmed_at, reason, preview_hash, dataset_keys=None
):
    plan = preview_curated_batch(
        session, user, loaded, confirmed_at=confirmed_at, reason=reason, dataset_keys=dataset_keys
    )
    if plan["preview_hash"] != preview_hash:
        raise ImportConflictError("batch preview changed; preview again before applying")
    user_id, tenant_id = user.id, user.tenant_id
    results = []
    for entry in plan["companies"]:
        set_request_context(session, user_id, tenant_id)
        actor = session.get(User, user_id)
        try:
            result = apply_curated_import(
                session,
                actor,
                company_selection(loaded, entry["company_key"], dataset_keys),
                confirmed_at=confirmed_at,
                reason=reason,
                preview_hash=entry["preview"]["preview_hash"],
            )
        except ImportConflictError as error:
            session.rollback()
            result = {"status": "conflict", "reason": str(error)}
        results.append({"company_key": entry["company_key"], **result})
    set_request_context(session, user_id, tenant_id)
    note_key = digest(
        [
            "curated-batch-notes",
            str(tenant_id),
            str(user_id),
            loaded.dataset_key,
            dataset_keys or {},
            loaded.file_hash,
            loaded.company_keys,
        ]
    )
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(note_key[:15], 16)})
    if not session.scalar(select(UsageLedger.id).where(UsageLedger.idempotency_key == note_key)):
        session.add(
            UsageLedger(
                tenant_id=tenant_id,
                provider="curated_workbook_import",
                operation="curated_batch_notes",
                external_calls=0,
                input_tokens=0,
                output_tokens=0,
                estimated_cost=Decimal("0"),
                idempotency_key=note_key,
                metrics={
                    "file_hash": loaded.file_hash,
                    "dataset_key": loaded.dataset_key,
                    "dataset_keys": dataset_keys or {},
                    "preview_hash": preview_hash,
                    "notes": list(loaded.notes),
                    "issues": plan["issues"],
                    "companies": results,
                },
            )
        )
    session.commit()
    return {
        "status": "completed_with_unresolved"
        if plan["issues"]
        or any(r["status"] in {"conflict", "completed_with_unresolved"} for r in results)
        else "completed",
        "companies": results,
        "record_count": len(loaded.records),
        "notes": list(loaded.notes),
        "issues": plan["issues"],
        "external_calls": 0,
    }
