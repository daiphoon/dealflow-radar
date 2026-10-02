import copy
from types import SimpleNamespace

import pytest

from scripts.verify_ci_partitions import REQUIRED_JOBS, verify_partitions, verify_results
from tests.support.ci_partition import group_for


def test_ci_partition_coverage_and_no_duplicates():
    records = [
        {"group": group, "commit": "same", "collected": ["a", "b", "c"], "selected": [node]}
        for group, node in zip(("python", "postgres", "release"), ("a", "b", "c"), strict=True)
    ]
    assert verify_partitions(records)["selected"] == 3
    for kind in ("missing", "overlap", "sha", "collection"):
        changed = copy.deepcopy(records)
        if kind == "missing":
            changed[0]["selected"] = []
        elif kind == "overlap":
            changed[0]["selected"] = ["b"]
        elif kind == "sha":
            changed[0]["commit"] = "other"
        else:
            changed[0]["collected"] = ["a"]
        with pytest.raises(ValueError):
            verify_partitions(changed)


@pytest.mark.parametrize("outcome", ["failure", "cancelled", "skipped", None])
def test_verify_never_accepts_unsuccessful_required_job(outcome):
    results = dict.fromkeys(REQUIRED_JOBS, "success")
    verify_results(results)
    results["postgres"] = outcome
    with pytest.raises(ValueError):
        verify_results(results)


def test_actual_marker_parameter_and_release_boundary():
    def item(nodeid, marker=False):
        return SimpleNamespace(
            nodeid=nodeid,
            path=nodeid.split("::")[0],
            get_closest_marker=lambda _: marker,
        )

    assert group_for(item("tests/x.py::case[postgresql-a]")) == "postgres"
    assert group_for(item("tests/x.py::rls", True)) == "postgres"
    assert group_for(item("tests/x.py::case[sqlite-a]")) == "python"
    assert group_for(item("tests/integration/test_m1_safe_degrade.py::case")) == "release"
