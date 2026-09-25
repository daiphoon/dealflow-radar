"""用相同权限和限额导出单页诊断；不接受 SQL、URL 或证据文件路径。"""

import argparse
import json
import os
from pathlib import Path
from uuid import UUID

from backend.app.diagnostics import TOOLS, DiagnosticService, Principal, build_diagnostic_engine
from scripts.research_benchmark import write_private


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", choices=TOOLS)
    parser.add_argument("--parameters", default="{}")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(Path("data/private").resolve()):
        parser.error("export must remain under data/private")
    record = json.loads(os.environ["MCP_EXPORT_PRINCIPAL"])
    principal = Principal(
        record["principal_id"],
        frozenset(UUID(v) for v in record["company_ids"]),
        record["expires_at"],
    )
    engine = build_diagnostic_engine(os.environ["MCP_DATABASE_URL"])
    try:
        service = DiagnosticService(
            engine,
            os.environ["MCP_CURSOR_SECRET"].encode(),
            budget_path=os.environ["MCP_BUDGET_FILE"],
        )
        result = service.call(principal, args.tool, json.loads(args.parameters))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_private(args.output, result, exclusive=True)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
