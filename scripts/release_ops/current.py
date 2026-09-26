"""Pre-open rehearsal and atomic pointer commit under one persistent attempt lock."""

import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

from .evidence import package_sha256, write_json
from .identity import verify_identity


@contextmanager
def release_lock(root, attempt):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", attempt):
        raise ValueError("invalid attempt")
    root = Path(root).resolve(strict=True)
    fd = os.open(root / ".release.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = os.read(fd, 200).decode().strip()
        if previous and previous != attempt:
            raise RuntimeError("different_attempt_owns_release_lock")
        if not previous:
            os.write(fd, attempt.encode())
            os.fsync(fd)
        yield
    finally:
        os.close(fd)


def _paths(root, target):
    root = Path(root).resolve(strict=True)
    if not re.fullmatch(r"[a-f0-9]{40}", target):
        raise ValueError("target must be approved business SHA")
    release = root / "releases" / target
    if (
        release.resolve(strict=True) != release
        or not release.is_dir()
        or release.stat().st_dev != root.stat().st_dev
    ):
        raise ValueError("release path or filesystem invalid")
    return root, release


def prepare(root, attempt, target, expected_old, identities, ops_sha):
    """All identity checks precede public opening. This never changes current."""
    if ops_sha != package_sha256():
        raise PermissionError("ops package differs from frozen code")
    if len(identities) != 2 or {i[0].get("role") for i in identities} != {"api", "frontend"}:
        return {"status": "BLOCKED", "actual": "two_application_identities_required"}
    root, release = _paths(root, target)
    with release_lock(root, attempt):
        results = [verify_identity(*identity) for identity in identities]
        if not results or any(r["status"] != "PASS" for r in results):
            return {
                "status": "BLOCKED"
                if any(r["status"] == "BLOCKED" for r in results)
                else "INCONCLUSIVE",
                "identity_results": results,
            }
        if any(r["revision"] != target for r in results):
            return {"status": "BLOCKED", "actual": "business_revision_mismatch"}
        current = root / "current"
        if not current.is_symlink() or str(current.resolve(strict=True)) != expected_old:
            return {"status": "BLOCKED", "actual": "unexpected_current"}
        # Rehearse the same filesystem primitive on disposable links under the lock.
        source = root / (".probe-" + attempt)
        dest = root / (".probe-result-" + attempt)
        if source.exists() or source.is_symlink() or dest.exists() or dest.is_symlink():
            raise FileExistsError("rehearsal residue requires explicit inspection")
        primary = None
        try:
            source.symlink_to(release)
            os.replace(source, dest)
            if dest.resolve() != release:
                raise RuntimeError("link readback mismatch")
        except Exception as exc:
            primary = exc
            raise
        finally:
            for path in (source, dest):
                try:
                    if path.is_symlink():
                        path.unlink()
                except OSError as cleanup:
                    if primary is not None:
                        primary.add_note(f"rehearsal cleanup also failed: {cleanup}")
                    else:
                        raise
        result = {
            "status": "PASS",
            "attempt": attempt,
            "business_sha": target,
            "target": str(release),
            "expected_old": expected_old,
            "root": str(root),
            "target_inode": release.stat().st_ino,
            "device": root.stat().st_dev,
            "ops_package_sha256": ops_sha,
            "identity_results": results,
        }
        return result


def commit(prepared, authorization, public_evidence):
    """No second identity formula; only a frozen intent and atomic link/readback."""
    if prepared["ops_package_sha256"] != package_sha256():
        raise PermissionError("ops package changed after preparation")
    for key in ("attempt", "business_sha", "ops_package_sha256"):
        if prepared[key] != authorization[key]:
            raise PermissionError("authorization binding mismatch")
    if "commit_current" not in authorization["allowed_actions"] or prepared["status"] != "PASS":
        raise PermissionError("commit not authorized/prepared")
    intent_hash = hashlib.sha256(json.dumps(prepared, sort_keys=True).encode()).hexdigest()
    if authorization["prepared_sha256"] != intent_hash:
        raise PermissionError("prepared intent changed")
    if (
        public_evidence.get("attempt") != prepared["attempt"]
        or public_evidence.get("status") != "PASS"
        or not public_evidence.get("browser_reports_read")
        or not public_evidence.get("temporary_routes_removed")
        or public_evidence.get("continuous_successes", 0) < 2
    ):
        raise PermissionError("public acceptance incomplete")
    root, release = _paths(prepared["root"], prepared["business_sha"])
    with release_lock(root, prepared["attempt"]):
        if (
            release.stat().st_ino != prepared["target_inode"]
            or root.stat().st_dev != prepared["device"]
        ):
            raise RuntimeError("prepared target changed")
        current = root / "current"
        if not current.is_symlink():
            raise RuntimeError("current not a symlink")
        if current.resolve() == release:
            return {"status": "PASS", "actual": "already_committed", "current": str(release)}
        if str(current.resolve(strict=True)) != prepared["expected_old"]:
            raise RuntimeError("current changed concurrently")
        temp = root / (".current-" + prepared["attempt"])
        if temp.is_symlink():
            if temp.resolve() != release:
                raise RuntimeError("unexpected temporary link")
        elif temp.exists():
            raise RuntimeError("temporary path occupied")
        else:
            temp.symlink_to(release)
        os.replace(temp, current)
        fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        if current.resolve() != release:
            raise RuntimeError("current readback failed")
        return {
            "status": "PASS",
            "actual": "committed",
            "current": str(release),
            "previous": prepared["expected_old"],
        }


def save_prepared(path, prepared):
    write_json(path, prepared)
    return hashlib.sha256(json.dumps(prepared, sort_keys=True).encode()).hexdigest()
