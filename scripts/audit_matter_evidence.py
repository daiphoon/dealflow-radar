"""本地只读核对旧 matter 哈希及其依赖；不提供自动重写操作。"""

import argparse
import json
from collections import Counter

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from backend.app.evidence_integrity import excerpt_hash_status
from backend.app.models import EventEvidence, EventFactSupport, RawDocument


def audit(session):
    result = []
    for row in session.scalars(select(EventEvidence)):
        if (row.display_detail_payload or {}).get("schema_version") != "matter-v1":
            continue
        doc = session.get(RawDocument, row.raw_document_id) if row.raw_document_id else None
        status = excerpt_hash_status(row, (doc.payload or {}).get("excerpt") if doc else None)
        result.append(
            {
                "evidence_id": str(row.id),
                "hash_status": status,
                "shared_dependents": session.scalar(
                    select(func.count())
                    .select_from(EventEvidence)
                    .where(EventEvidence.source_event_evidence_id == row.id)
                ),
                "fact_support_dependents": session.scalar(
                    select(func.count())
                    .select_from(EventFactSupport)
                    .where(EventFactSupport.event_evidence_id == row.id)
                ),
            }
        )
    return {
        "dry_run": True,
        "writes": 0,
        "counts": dict(Counter(r["hash_status"] for r in result)),
        "evidence": result,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    url = make_url(args.database_url)
    if url.get_backend_name() != "sqlite" and url.host not in {"localhost", "127.0.0.1"}:
        parser.error("仅允许显式本地数据库；生产扫描另行授权")
    if url.get_backend_name() == "sqlite":
        from pathlib import Path

        if not url.database or not Path(url.database).is_file():
            parser.error("本地扫描要求已存在的 SQLite 文件，不自动创建数据库")
    engine = create_engine(url)
    with Session(engine) as session:
        if url.get_backend_name() == "postgresql":
            from sqlalchemy import text

            session.execute(text("SET TRANSACTION READ ONLY"))
        else:
            from sqlalchemy import text

            session.execute(text("PRAGMA query_only=ON"))
        print(json.dumps(audit(session), ensure_ascii=False, indent=2))
    engine.dispose()


if __name__ == "__main__":
    main()
