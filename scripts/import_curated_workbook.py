"""本地负责人确认资料入口；默认只预览，显式 apply 才写入。"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from uuid import UUID

from backend.app.curated_import import apply_curated_import, preview_curated_import
from backend.app.curated_workbook import CuratedWorkbookProvider
from backend.app.database import build_engine, build_session_factory, set_request_context
from backend.app.models import User


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True)
    parser.add_argument("--dataset-key", required=True, help="同一资料库的稳定标识，更正版继续沿用")
    parser.add_argument("--company-key", action="append", default=[])
    parser.add_argument(
        "--mode", choices=["identity_only", "initial_data"], default="identity_only"
    )
    parser.add_argument("--confirmed-at", required=True, type=datetime.fromisoformat)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--request-id", type=UUID)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--preview-hash")
    args = parser.parse_args()
    if args.apply and not args.preview_hash:
        parser.error("--apply requires the reviewed --preview-hash")
    database_url, user_id = os.environ.get("DATABASE_URL"), os.environ.get("IMPORT_USER_ID")
    tenant_id = os.environ.get("IMPORT_TENANT_ID")
    if not database_url or not user_id or not tenant_id:
        parser.error("DATABASE_URL, IMPORT_USER_ID and IMPORT_TENANT_ID are required")
    loaded = CuratedWorkbookProvider(
        args.file,
        dataset_key=args.dataset_key,
        mode=args.mode,
        company_keys=tuple(args.company_key),
    ).load()
    engine = build_engine(database_url)
    try:
        with build_session_factory(engine)() as session:
            set_request_context(session, UUID(user_id), UUID(tenant_id))
            user = session.get(User, UUID(user_id))
            if user is None:
                raise ValueError("IMPORT_USER_ID must identify an accessible active curator")
            kwargs = {
                "confirmed_at": args.confirmed_at,
                "reason": args.reason,
                "request_id": args.request_id,
            }
            if args.apply:
                result = apply_curated_import(
                    session, user, loaded, preview_hash=args.preview_hash, **kwargs
                )
            else:
                with session.no_autoflush:
                    result = preview_curated_import(session, user, loaded, **kwargs)
                session.rollback()
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
