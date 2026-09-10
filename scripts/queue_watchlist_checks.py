"""Inspect or enqueue a bounded batch. This command never calls external providers."""

import argparse
import json

from backend.app.config import Settings
from backend.app.watchlist_monitoring import queue_due_watch_checks
from scripts.run_web_research_worker import _required_uuid, _with_worker_session


def main():
    parser = argparse.ArgumentParser(description="Inspect due checks for watched public companies")
    parser.add_argument("--enqueue", action="store_true", help="enqueue at most the configured cap")
    args = parser.parse_args()
    settings = Settings.from_env()
    result = _with_worker_session(
        settings,
        _required_uuid("WEB_RESEARCH_WORKER_USER_ID"),
        _required_uuid("WORKER_TENANT_ID"),
        lambda session, user: queue_due_watch_checks(
            session, user, settings.web_research_policy, dry_run=not args.enqueue
        ),
    )
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
