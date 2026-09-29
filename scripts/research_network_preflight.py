"""独立环境验收入口；不创建应用、不读数据库、不构造任何 Research Provider。"""

import argparse
import json

from backend.app.research_network import (
    PREFLIGHT_POLICY,
    PREFLIGHT_TARGETS,
    run_research_network_preflight,
)


def main():
    parser = argparse.ArgumentParser(description="固定免费 HTTPS 研究环境预检")
    parser.add_argument("--execute", action="store_true", help="执行固定三站；缺省只显示调用边界")
    args = parser.parse_args()
    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "targets": PREFLIGHT_TARGETS,
                    "provider_calls": 0,
                    "model_calls": 0,
                    "database_connections": 0,
                    "cash_cost": "0",
                    "max_http_requests": PREFLIGHT_POLICY.max_requests_per_run,
                    "max_bytes": PREFLIGHT_POLICY.max_download_bytes_per_run,
                },
                ensure_ascii=False,
            )
        )
        return
    result = run_research_network_preflight()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if result["status"] == "research_network_ready" else 2)


if __name__ == "__main__":
    main()
