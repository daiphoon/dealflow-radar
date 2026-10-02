"""按实际收集节点分片，RLS参数不会落入无PG的job。"""

import json
import os
from pathlib import Path

import pytest

RELEASE_FILES = {
    "test_m1_safe_degrade.py",
    "test_ops_release_compose.py",
    "test_ops_release_containers.py",
}


def group_for(item):
    if Path(str(item.path)).name in RELEASE_FILES:
        return "release"
    if item.get_closest_marker("postgres") or "[postgresql" in item.nodeid:
        return "postgres"
    return "python"


def pytest_addoption(parser):
    parser.addoption("--ci-group", choices=("python", "postgres", "release"))


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    group = config.getoption("--ci-group")
    if not group:
        return
    complete = [item.nodeid for item in items]
    selected = [item for item in items if group_for(item) == group]
    deselected = [item for item in items if group_for(item) != group]
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)
    path = os.environ.get("CI_PARTITION_PATH")
    if path:
        Path(path).write_text(
            json.dumps(
                {
                    "group": group,
                    "collected": complete,
                    "selected": [i.nodeid for i in selected],
                    "commit": os.environ.get("GITHUB_SHA", "local"),
                },
                indent=2,
            )
            + "\n"
        )
