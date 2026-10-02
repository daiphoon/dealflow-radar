"""最终Verify拒绝缺失、重叠、跨SHA或未成功的必需job。"""

import json
import sys
from pathlib import Path

REQUIRED_JOBS = {"static_frontend", "python", "postgres", "release"}


def verify_results(results):
    if set(results) != REQUIRED_JOBS or any(r != "success" for r in results.values()):
        raise ValueError("required_job_not_success")


def verify_partitions(records):
    if len(records) != 3 or {r["group"] for r in records} != {"python", "postgres", "release"}:
        raise ValueError("partition_missing_or_duplicate")
    reference = records[0]
    expected = set(reference["collected"])
    seen = set()
    for record in records:
        selected = record["selected"]
        if record["commit"] != reference["commit"] or set(record["collected"]) != expected:
            raise ValueError("partition_sha_or_collection_drift")
        if len(selected) != len(set(selected)) or seen & set(selected):
            raise ValueError("partition_overlap")
        seen.update(selected)
    if seen != expected:
        raise ValueError("partition_missing_or_unknown_nodes")
    return {"collected": len(expected), "selected": len(seen), "commit": reference["commit"]}


if __name__ == "__main__":
    import os

    verify_results(json.loads(os.environ["CI_JOB_RESULTS"]))
    print(
        json.dumps(
            verify_partitions(
                [json.loads(p.read_text()) for p in Path(sys.argv[1]).rglob("partition.json")]
            )
        )
    )
