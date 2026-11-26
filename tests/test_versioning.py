import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest


def test_windows_contender_retries_without_writing_a_locked_empty_file(tmp_path, monkeypatch):
    """Windows denies writes to a region owned by another lock handle."""
    import os
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from agentcad import versioning

    path = tmp_path / "write.lock"
    path.touch()
    attempts = []
    real_open = type(path).open

    def protected_open(selected, *args, **kwargs):
        handle = real_open(selected, *args, **kwargs)
        proxy = MagicMock(wraps=handle)
        proxy.__enter__.return_value = proxy
        proxy.__exit__.side_effect = handle.__exit__
        proxy.write.side_effect = PermissionError("another handle owns byte zero")
        proxy.flush.side_effect = PermissionError("another handle owns byte zero")
        return proxy

    def locking(fd, mode, length):
        assert os.lseek(fd, 0, os.SEEK_CUR) == 0 and length == 1
        attempts.append(mode)
        if len(attempts) == 1:
            raise BlockingIOError("another handle owns byte zero")

    monkeypatch.setattr(type(path), "open", protected_open)
    monkeypatch.setattr(versioning, "os", SimpleNamespace(name="nt"))
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(
        locking=locking, LK_NBLCK=1, LK_UNLCK=0,
    ))
    with versioning._file_lock(path):
        assert path.stat().st_size == 0
    assert attempts == [1, 1, 0]


def test_version_lock_is_released_when_its_process_is_killed(tmp_path):
    from agentcad.versioning import _version_lock

    script = """
import sys, time
from pathlib import Path
from agentcad.versioning import _version_lock
with _version_lock(Path(sys.argv[1])):
    print('locked', flush=True)
    time.sleep(120)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout.readline().strip() == "locked"
        child.kill()
        child.wait(timeout=10)
        started = time.monotonic()
        with _version_lock(tmp_path):
            assert time.monotonic() - started < 2
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        child.stdout.close()


def test_concurrent_reservations_get_unique_numbers_and_directories(tmp_path):
    from agentcad.versioning import reserve_version

    (tmp_path / "agentcad.json").write_text(
        json.dumps(
            {
                "name": "concurrent",
                "versions": [],
            }
        )
    )

    def reserve(index):
        return reserve_version(tmp_path, f"part-{index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(reserve, range(8)))

    assert sorted(r.number for r in reservations) == list(range(1, 9))
    assert len({r.path for r in reservations}) == 8
    assert all(r.path.is_dir() for r in reservations)


def test_atomic_json_failure_preserves_previous_document(tmp_path):
    from agentcad.versioning import atomic_write_json

    target = tmp_path / "meta.json"
    atomic_write_json(target, {"status": "success", "version": 1})

    with pytest.raises(TypeError):
        atomic_write_json(target, {"not_json": object()})

    assert json.loads(target.read_text()) == {
        "status": "success",
        "version": 1,
    }
    assert list(tmp_path.glob(".meta.json.*.tmp")) == []


def test_concurrent_core_commits_merge_manifest_entries(tmp_path):
    from agentcad.versioning import commit_version, reserve_version

    manifest_path = tmp_path / "agentcad.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "concurrent",
                "versions": [],
            }
        )
    )
    reservations = [reserve_version(tmp_path, f"part-{index}") for index in range(8)]

    def commit(reservation):
        meta = {
            "status": "success",
            "version": reservation.number,
            "label": reservation.label,
        }
        entry = {
            "version": reservation.number,
            "label": reservation.label,
            "status": "success",
            "path": f"{reservation.dir_name}/",
        }
        commit_version(
            reservation,
            meta,
            entry,
            advance_current=True,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(commit, reservations))

    manifest = json.loads(manifest_path.read_text())
    assert [entry["version"] for entry in manifest["versions"]] == list(range(1, 9))
    assert len({entry["path"] for entry in manifest["versions"]}) == 8
    assert all(
        json.loads((reservation.path / "meta.json").read_text())["status"] == "success"
        for reservation in reservations
    )


def test_commit_version_conflict_does_not_overwrite_metadata(tmp_path):
    from agentcad.versioning import (
        VersionConflictError,
        VersionReservation,
        commit_version,
    )

    manifest_path = tmp_path / "agentcad.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "conflict",
                "versions": [
                    {
                        "version": 1,
                        "label": "registered",
                        "status": "success",
                        "path": "v1_registered/",
                    }
                ],
            }
        )
    )
    orphan = tmp_path / "v1_orphan"
    orphan.mkdir()
    reservation = VersionReservation(1, "orphan", "v1_orphan", orphan)

    with pytest.raises(VersionConflictError):
        commit_version(
            reservation,
            {"status": "success", "important": "new metadata"},
            {
                "version": 1,
                "label": "orphan",
                "status": "success",
                "path": "v1_orphan/",
            },
            advance_current=False,
        )

    assert not (orphan / "meta.json").exists()
    assert json.loads(manifest_path.read_text())["versions"][0]["path"] == (
        "v1_registered/"
    )
