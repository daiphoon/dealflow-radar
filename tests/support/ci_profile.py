"""可选 pytest 性能证据；仅记录阶段、操作类型和资源汇总。"""

import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import shutil
import sys
import threading
import time
from collections import Counter
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text

_lock = threading.Lock()
_stop = threading.Event()
_counts = Counter()
_times = Counter()
_rows = []
_output = None


def emit(kind, **values):
    if _output is None:
        return
    with _lock:
        with _output.open("a") as stream:
            stream.write(json.dumps({"kind": kind, "at": time.time(), **values}) + "\n")


def tracked(label, function):
    def wrapped(*args, **kwargs):
        start = time.monotonic()
        try:
            return function(*args, **kwargs)
        finally:
            _counts[label] += 1
            _times[label] += time.monotonic() - start

    return wrapped


def before_sql(conn, cursor, statement, parameters, context, executemany):
    words = statement.lstrip().upper().split()
    if len(words) >= 2 and words[0] in {"CREATE", "DROP"}:
        label = " ".join(words[:2])
        if words[1] in {"DATABASE", "ROLE"}:
            _counts[label] += 1
            context._ci_profile_start = (label, time.monotonic())


def after_sql(conn, cursor, statement, parameters, context, executemany):
    if hasattr(context, "_ci_profile_start"):
        label, start = context._ci_profile_start
        _times[label] += time.monotonic() - start


def sample():
    admin = os.getenv("DATABASE_ADMIN_URL")
    engine = create_engine(admin) if admin else None
    try:
        while not _stop.wait(10):
            usage = resource.getrusage(resource.RUSAGE_SELF)
            values = {
                "cpu_user": usage.ru_utime,
                "cpu_system": usage.ru_stime,
                "maxrss": usage.ru_maxrss,
                "block_in": usage.ru_inblock,
                "block_out": usage.ru_oublock,
                "disk_free": shutil.disk_usage(Path.cwd()).free,
                "load": os.getloadavg(),
            }
            if engine:
                try:
                    with engine.connect() as conn:
                        values["pg_waits"] = [
                            list(row)
                            for row in conn.execute(
                                text(
                                    "SELECT state, wait_event_type, count(*) "
                                    "FROM pg_stat_activity WHERE backend_type='client backend' "
                                    "GROUP BY state,wait_event_type"
                                )
                            )
                        ]
                except Exception as exc:
                    values["pg_sampling_error"] = type(exc).__name__
            emit("resources", **values)
    finally:
        if engine:
            engine.dispose()


def pytest_configure(config):
    global _output
    destination = os.getenv("CI_PROFILE_PATH")
    if not destination:
        return
    _output = Path(destination)
    _output.parent.mkdir(parents=True, exist_ok=True)
    _output.write_text("")
    emit(
        "runtime",
        python=sys.version,
        platform=platform.platform(),
        cpus=os.cpu_count(),
        dependencies={
            name: importlib.metadata.version(name)
            for name in ("pytest", "SQLAlchemy", "alembic", "psycopg")
        },
        lock_sha256=hashlib.sha256(Path("uv.lock").read_bytes()).hexdigest(),
    )
    event.listen(Engine, "before_cursor_execute", before_sql)
    event.listen(Engine, "after_cursor_execute", after_sql)
    from alembic import command

    command.upgrade = tracked("migration", command.upgrade)
    threading.Thread(target=sample, daemon=True).start()


def pytest_collection_modifyitems(config, items):
    # 仅第一轮性能诊断使用；常规分组和风险覆盖不经过这个筛选。
    database = os.getenv("CI_BASELINE_SUBSET_DATABASE")
    if not database:
        return
    selected, deselected = [], []
    cases = {
        "expected_close",
        "funds_not_received",
        "unrelated_denial",
        "completed",
        "funds_positive",
    }
    for item in items:
        params = getattr(item, "callspec", None)
        values = params.params if params else {}
        case = values.get("name")
        case_name = case[0] if isinstance(case, tuple) else case
        keep = values.get("database") == database and (
            "modal_family" in item.name or case_name in cases
        )
        (selected if keep else deselected).append(item)
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected


def pytest_collection_finish(session):
    emit("collected", nodeids=[item.nodeid for item in session.items])
    if _output is None:
        return
    from backend.app.services import seed_demo_entities
    from scripts.bootstrap_local_database import bootstrap_application_role

    wrapper = tracked("seed", seed_demo_entities)
    for module in list(sys.modules.values()):
        if module and getattr(module, "__name__", "").startswith(("tests", "backend")):
            if getattr(module, "seed_demo_entities", None) is seed_demo_entities:
                module.seed_demo_entities = wrapper
            if getattr(module, "bootstrap_application_role", None) is bootstrap_application_role:
                module.bootstrap_application_role = tracked(
                    "bootstrap_role", bootstrap_application_role
                )


def pytest_runtest_logstart(nodeid, location):
    emit("start", nodeid=nodeid)


def pytest_runtest_logreport(report):
    row = dict(
        nodeid=report.nodeid, phase=report.when, seconds=report.duration, outcome=report.outcome
    )
    _rows.append(row)
    emit("phase", **row)


def pytest_sessionfinish(session, exitstatus):
    _stop.set()
    totals = Counter()
    for row in _rows:
        totals[row["phase"]] += row["seconds"]
    emit(
        "summary",
        exitstatus=int(exitstatus),
        phases=dict(totals),
        operations=dict(_counts),
        operation_seconds=dict(_times),
        top20=sorted(_rows, key=lambda row: row["seconds"], reverse=True)[:20],
    )
