"""CLI: observe and plan never mutate; apply is bound to reviewed authorization."""

import argparse
import json
from dataclasses import replace
from pathlib import Path

from .actions import apply_action
from .compose import expected_contract
from .current import commit, prepare, save_prepared
from .evidence import Recorder, redact
from .identity import verify_identity
from .metadata import observe_metadata
from .probes import Contract, failure_policy, http_probe, wait_ready
from .snapshot import observe_daemon, observe_health


def load(path):
    return json.loads(Path(path).read_text())


def identity_input(path, blobs):
    data = load(path)
    raw = {
        f"sha256:{f.name}": f.read_bytes()
        for f in Path(blobs).iterdir()
        if f.is_file() and len(f.name) == 64
    }
    return data["approved"], raw, data["observed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=[
            "observe-identity",
            "observe-http",
            "plan",
            "prepare-current",
            "apply-current",
            "apply-step",
            "observe-daemon",
            "observe-health",
            "observe-metadata",
        ],
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--blobs")
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--authorization")
    parser.add_argument(
        "--checkpoint", help="reviewed gate ID; use a fresh suffix for repeat observations"
    )
    args = parser.parse_args()
    recorder = Recorder(args.evidence, args.attempt)
    checkpoint = args.checkpoint or args.operation.removeprefix("observe-")
    try:
        result = dispatch(args, recorder, checkpoint)
    except Exception as exc:

        def failed(error=exc):
            raise error

        result = recorder.check(
            checkpoint + "-input-error",
            "operator",
            "release",
            "valid reviewed input",
            "input_validation",
            failed,
        )
    print(json.dumps(redact(result), ensure_ascii=False))
    return {"PASS": 0, "NOT_READY": 3, "BLOCKED": 1, "CHECK_ERROR": 2, "INCONCLUSIVE": 4}[
        result["status"]
    ]


def dispatch(args, recorder, checkpoint):
    if args.operation == "observe-metadata":
        data = load(args.input)
        if checkpoint != data["kind"]:
            raise ValueError("metadata checkpoint must match the reviewed kind")
        result = recorder.check(
            checkpoint,
            "docker_host",
            "release",
            expected_contract(data)
            if data["kind"] == "compose-frozen"
            else data.get("expected", {"kind": data["kind"]}),
            "readonly_metadata",
            lambda: observe_metadata(data),
        )
    elif args.operation == "observe-health":
        data = load(args.input)
        result = wait_ready(
            lambda timeout: observe_health(data["container"], data["expected_test"], timeout),
            lambda index, probe: recorder.check(
                f"{checkpoint}-{index}",
                "docker_host",
                data["container"],
                data["expected_test"],
                "compose_health",
                probe,
            ),
            **data.get("bounds", {}),
        )
    elif args.operation == "observe-daemon":
        data = load(args.input)
        result = recorder.check(
            checkpoint,
            "docker_host",
            data.get("container", "safety-control image"),
            "whitelisted metadata",
            "docker_readonly",
            lambda: {
                "status": "PASS",
                "actual": observe_daemon(data["reference"], data.get("container")),
            },
        )
    elif args.operation == "observe-identity":
        result = recorder.check(
            checkpoint,
            "operator",
            "selected_container",
            "approved immutable registry chain",
            "registry_daemon_binding",
            lambda: verify_identity(*identity_input(args.input, args.blobs)),
        )
    elif args.operation == "observe-http":
        data = load(args.input)
        contract = Contract(**data["contract"])
        result = wait_ready(
            lambda timeout: http_probe(replace(contract, timeout=timeout)),
            lambda index, probe: recorder.check(
                f"{checkpoint}-{index}",
                data["observer_location"],
                contract.layer,
                data["contract"],
                "bounded_http",
                probe,
            ),
            **data.get("bounds", {}),
        )
    elif args.operation == "plan":
        data = load(args.input)
        result = recorder.check(
            checkpoint,
            "operator",
            "release",
            "no state change",
            "decision",
            lambda: {
                "status": "PASS",
                "decisions": [
                    {
                        "checkpoint": r["checkpoint_id"],
                        "status": r["status"],
                        "action": failure_policy(
                            r["status"],
                            public_open=data["public_open"],
                            safety_required=r.get("safety_required", True),
                            archival_exception_approved=data.get(
                                "archival_exception_approved", False
                            ),
                        ),
                    }
                    for r in data["results"]
                ],
                "apply_executed": False,
            },
        )
    elif args.operation == "prepare-current":
        data = load(args.input)
        result = recorder.check(
            checkpoint,
            "host",
            "current",
            "identities PASS before opening; pointer unchanged",
            "link_rehearsal",
            lambda: prepare(
                data["root"],
                args.attempt,
                data["target"],
                data["expected_old"],
                [identity_input(p, args.blobs) for p in data["identities"]],
                data["ops_package_sha256"],
            ),
        )
        if result["status"] == "PASS":
            # The exact object, including evidence, is subsequently authorized by hash.
            result["prepared_sha256"] = save_prepared(data["output"], result)
    elif args.operation == "apply-step":
        if not args.authorization:
            raise PermissionError("apply requires explicit reviewed authorization file")
        data = load(args.input)
        result = apply_action(
            data["binding"], load(args.authorization), data["action"], data["gates"], recorder
        )
    else:
        if not args.authorization:
            raise PermissionError("apply requires explicit reviewed authorization file")
        data = load(args.input)
        result = recorder.check(
            checkpoint,
            "host",
            "current",
            "atomic commit of prevalidated intent",
            "atomic_link",
            lambda: commit(
                load(data["prepared"]), load(args.authorization), load(data["public_evidence"])
            ),
        )
    if args.operation in {"observe-health", "observe-http"}:
        summary = {
            k: v
            for k, v in result.items()
            if k
            not in {
                "attempt_id",
                "checkpoint_id",
                "evidence",
                "evidence_sha256",
                "started_at",
                "finished_at",
                "duration_ms",
            }
        }
        summary["final_probe_evidence"] = result.get("evidence")
        result = recorder.check(
            checkpoint,
            "docker_host" if args.operation == "observe-health" else data["observer_location"],
            data.get("container", "http"),
            {"bounds": data.get("bounds", {}), "continuous_successes": 2},
            "readiness_summary",
            lambda: summary,
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
