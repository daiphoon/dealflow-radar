"""在自动创建的 SQLite 临时库运行虚构正文演示，不读取项目 .env 或访问网络。"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/private/reliability-demo"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    private = (root / "data/private").resolve()
    if not output.is_relative_to(private):
        parser.error("演示输出必须位于已忽略的 data/private 目录")
    env = dict(os.environ)
    for key in ("DATABASE_URL", "DATABASE_ADMIN_URL", "POSTGRES_RLS_DATABASE_URL"):
        env.pop(key, None)
    for key in (
        "EXTERNAL_CALLS_ENABLED",
        "PAID_API_CALLS_ENABLED",
        "AUTO_REFRESH_ENABLED",
        "AUTO_PUBLISH_ENABLED",
        "RUN_EXTERNAL_TESTS",
    ):
        env[key] = "false"
    env["RELIABILITY_DEMO_OUTPUT"] = str(output)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/integration/test_matter_reliability.py::test_offline_walkthrough_conflict_retraction_and_current_view[sqlite]",
        ],
        cwd=root,
        env=env,
    )
    if result.returncode:
        raise SystemExit(result.returncode)
    print(f"虚构演示结果：{output / 'walkthrough-sqlite.json'}")


if __name__ == "__main__":
    main()
