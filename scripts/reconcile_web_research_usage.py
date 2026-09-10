"""Inspect or reconcile one platform web charge; never instantiate an external provider."""

import argparse
import json
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from backend.app import web_research_budget as budget
from backend.app.config import Settings
from backend.app.models import UsageLedger
from backend.app.services import user_has_role
from scripts.run_web_research_worker import _required_uuid, _with_worker_session


def main():
    parser = argparse.ArgumentParser(description="核对一条公开研究费用记录，默认只读")
    parser.add_argument("usage_id", type=UUID)
    parser.add_argument("--apply", action="store_true", help="按核对凭据结算并保留审计记录")
    parser.add_argument("--calls", type=int)
    parser.add_argument("--actual-cost", type=Decimal)
    parser.add_argument("--reference", help="账单或调用回执编号，不填密钥或私密正文")
    parser.add_argument("--reason", help="核对依据和原因")
    args = parser.parse_args()
    if args.apply and any(
        value is None for value in (args.calls, args.actual_cost, args.reference, args.reason)
    ):
        parser.error("--apply requires --calls, --actual-cost, --reference and --reason")

    def inspect_or_apply(session, user):
        if not user_has_role(session, user.id, "platform_admin"):
            raise PermissionError("platform_admin role required")
        if args.apply:
            row = budget.reconcile(
                session,
                user,
                args.usage_id,
                calls=args.calls,
                actual_cost=args.actual_cost,
                reference=args.reference,
                reason=args.reason,
            )
        else:
            row = session.scalar(
                select(UsageLedger).where(
                    UsageLedger.id == args.usage_id,
                    budget.platform_usage(),
                )
            )
            if row is None:
                raise ValueError("platform web usage not found")
        result = {
            "usage_id": str(row.id),
            "applied": args.apply,
            "state": row.usage_state,
            "cost_status": row.cost_status,
            "confirmed_calls": row.external_calls,
            "reserved_calls": row.reserved_calls,
            "amount": str(row.estimated_cost) if row.estimated_cost is not None else None,
            "reserved_cost": str(row.reserved_cost),
            "currency": "CNY",
            "external_calls_by_this_command": 0,
        }
        if args.apply:
            session.commit()
        return result

    result = _with_worker_session(
        Settings.from_env(),
        _required_uuid("WEB_RESEARCH_WORKER_USER_ID"),
        _required_uuid("WORKER_TENANT_ID"),
        inspect_or_apply,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
