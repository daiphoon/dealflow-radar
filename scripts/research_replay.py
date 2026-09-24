"""生产路径离线回放 manifest；不读密钥，不创建搜索/模型 Provider。"""

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from alembic.config import Config
from alembic.script import ScriptDirectory

from backend.app.research_evaluation import funnel, replay, scorecard
from scripts.research_benchmark import digest, write_private


def replay_file(input_path, output):
    if not output.resolve().is_relative_to(Path("data/private").resolve()):
        raise ValueError("replay results must remain in data/private")
    if input_path.stat().st_size > 1_000_000:
        raise ValueError("replay input too large")
    data = json.loads(input_path.read_text())
    if data.get("sample_origin") not in {"synthetic", "authorized_retained_text"}:
        raise ValueError("explicit sample origin required")
    cases = data["cases"]
    if not 1 <= len(cases) <= 100:
        raise ValueError("bounded case count required")
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for case in cases:
        body = case.get("body")
        if body is None:
            result = {
                "status": "not_retained",
                "reason": case.get("not_retained_reason", "not_recorded"),
            }
        else:
            if len(body) > 12000:
                raise ValueError("retained context limit")
            subject = SimpleNamespace(
                legal_name=case["legal_name"],
                aliases=tuple(case.get("aliases", [])),
                legal_aliases=tuple(case.get("legal_aliases", [])),
            )
            result = replay(subject, body, case.get("historical_or_mock_output"))
        results.append({"case_id": case["id"], **result, **funnel(case.get("observed_stages", {}))})
    manifest = {
        "schema_version": "production-replay-v1",
        "experiment": "retained_text_postprocessing",
        "application_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "working_tree_patch_sha256": digest(
            subprocess.check_output(["git", "diff", "--binary"], text=True)
        ),
        "source_file_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder in ("backend/app", "scripts", "migrations/versions")
            for path in sorted(Path(folder).glob("*.py"))
        },
        "migration": ScriptDirectory.from_config(Config("alembic.ini")).get_current_head(),
        "sample_origin": data["sample_origin"],
        "sample_count": len(cases),
        "input_sha256": digest(data),
        "reference_at": data.get("reference_at", "not_recorded"),
        "timezone": "Asia/Shanghai",
        "research_intent": "offline_regression",
        "event_window_days": data.get("event_window_days", "not_recorded"),
        "feature_flags": {"external_calls": False, "paid_calls": False, "auto_publish": False},
        "budget": {"external_calls": 0, "cash": 0},
        "search_response": "not_retained_no_search_in_this_experiment",
        "result_path": "results.json",
        "business_acceptance": "not_run",
        "scorecard": scorecard([case.get("frozen_judgment") for case in cases]),
        "cost_per_qualified_matter": None,
    }
    write_private(output / "results.json", results, exclusive=True)
    write_private(output / "manifest.json", manifest, exclusive=True)
    return {
        "manifest": str(output / "manifest.json"),
        "sample_count": len(cases),
        "external_calls": 0,
    }
