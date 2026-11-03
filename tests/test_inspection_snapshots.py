"""Immutable response lifetimes, byte pressure and cross-process read protection."""

from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
import os
from pathlib import Path

import pytest

from agentcad.geometry_manifest import _digest
from agentcad.inspection_snapshots import SnapshotStore, SnapshotExpired, SnapshotUnavailable, SnapshotResourceLimit
from agentcad.project import ProjectLayout
from agentcad.versioning import atomic_write_json


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("agentcad.inspection_snapshots._now", lambda: 1000)
    return SnapshotStore(ProjectLayout(tmp_path, tmp_path))


def _body(value="response"):
    return {"schema_version": 1, "payload": {"status": "success", "value": value}}


def _files(store):
    return list(store.directory.glob("delivery_*.json"))


def _policy(root, **settings):
    (root / "agentcad.toml").write_text("[inspection.snapshots]\n" + "\n".join(f"{key} = {json.dumps(value)}" for key, value in settings.items()))


def test_frozen_expiry_and_read_does_not_extend_lifetime(store, monkeypatch):
    ref = store.publish(_body())
    path = Path(ref["path"])
    before = path.read_bytes()
    assert ref["retention"]["may_evict_earlier"] and ref["retention"]["scope"] == "response_snapshot"
    monkeypatch.setattr("agentcad.inspection_snapshots._now", lambda: 1000 + store.max_age_seconds - 1)
    body, retrieved = store.read(ref["id"])
    assert body["payload"] == _body()["payload"]
    assert retrieved == ref and path.read_bytes() == before
    monkeypatch.setattr("agentcad.inspection_snapshots._now", lambda: 1000 + store.max_age_seconds)
    with pytest.raises(SnapshotExpired, match="cannot be regenerated"):
        store.read(ref["id"])
    assert not path.exists()


def test_quota_evicts_oldest_and_never_touches_durable_evidence(store, monkeypatch):
    protected = [store.directory.parent / name for name in ("geometry_manifest.json", "baseline.step", "source.step", "jobs/state.json", "import_requests/request.json")]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"durable evidence")
    first = store.publish(_body("a"))
    size = Path(first["path"]).stat().st_size
    store.max_bytes = size * 2
    monkeypatch.setattr("agentcad.inspection_snapshots._now", lambda: 1001)
    second = store.publish(_body("b"))
    monkeypatch.setattr("agentcad.inspection_snapshots._now", lambda: 1002)
    third = store.publish(_body("c"))
    assert not Path(first["path"]).exists()
    assert Path(second["path"]).exists() and Path(third["path"]).exists()
    assert sum(path.stat().st_size for path in _files(store)) <= store.max_bytes
    assert all(path.read_bytes() == b"durable evidence" for path in protected)
    with pytest.raises(SnapshotUnavailable, match="evicted"):
        store.read(first["id"])


def test_oversized_write_preserves_existing_snapshots(store):
    ref = store.publish(_body())
    before = Path(ref["path"]).read_bytes()
    store.max_bytes = len(before)
    with pytest.raises(SnapshotResourceLimit, match="above"):
        store.publish(_body("x" * 10000))
    assert Path(ref["path"]).read_bytes() == before
    assert len(_files(store)) == 1


def test_oversized_read_and_unrepresentable_expiry_preserve_existing_bytes(store):
    ref = store.publish(_body())
    before = Path(ref["path"]).read_bytes()
    store.max_bytes = 1
    with pytest.raises(SnapshotResourceLimit, match="before reading"):
        store.read(ref["id"])
    assert Path(ref["path"]).read_bytes() == before
    store.max_bytes = 10000
    store.max_age_seconds = 2 ** 100
    with pytest.raises(SnapshotUnavailable, match="unsupported expiry"):
        store.publish(_body("unrepresentable"))
    assert Path(ref["path"]).read_bytes() == before and len(_files(store)) == 1


def test_expired_removed_before_byte_eviction_and_cleanup_on_read(store, monkeypatch):
    store.max_age_seconds = 20
    oldest = store.publish(_body("a"))
    monkeypatch.setattr("agentcad.inspection_snapshots._now", lambda: 1010)
    live = store.publish(_body("b"))
    monkeypatch.setattr("agentcad.inspection_snapshots._now", lambda: 1021)
    store.read(live["id"])
    assert not Path(oldest["path"]).exists() and Path(live["path"]).exists()


def test_legacy_snapshot_migrates_without_changing_bytes(store, monkeypatch):
    body = _body()
    snapshot_id = "delivery_" + _digest(body)
    path = store.directory / (snapshot_id + ".json")
    atomic_write_json(path, {"id": snapshot_id, **body})
    os.utime(path, (900, 900))
    before = path.read_bytes()
    original, ref = store.read(snapshot_id)
    assert original == body and path.read_bytes() == before
    assert ref["retention"]["expires_at"]
    monkeypatch.setattr("agentcad.inspection_snapshots._now", lambda: 900 + store.max_age_seconds)
    with pytest.raises(SnapshotExpired):
        store.read(snapshot_id)


def test_config_changes_cannot_extend_frozen_expiry(store, monkeypatch):
    store.max_age_seconds = 10
    ref = store.publish(_body())
    _policy(store.directory.parents[1], max_age_seconds=10000)
    updated = SnapshotStore(ProjectLayout(store.directory.parents[1], store.directory.parents[1]))
    monkeypatch.setattr("agentcad.inspection_snapshots._now", lambda: 1011)
    with pytest.raises(SnapshotExpired):
        updated.read(ref["id"])


@pytest.mark.parametrize("settings", [{"max_bytes": 0}, {"max_age_seconds": -1}, {"max_bytes": True}, {"max_bytes": 1.5}, {"max_byes": 10}])
def test_invalid_config_is_actionable(tmp_path, settings):
    _policy(tmp_path, **settings)
    with pytest.raises(SnapshotUnavailable, match="inspection.snapshots"):
        SnapshotStore(ProjectLayout(tmp_path, tmp_path))


def test_nondefault_policy_and_root(tmp_path):
    _policy(tmp_path, max_age_seconds=10, max_bytes=8192)
    store = SnapshotStore(ProjectLayout(tmp_path, tmp_path / "CAD — owner's files"))
    ref = store.publish(_body())
    assert ref["retention"]["max_bytes"] == 8192 and ref["retention"]["max_age_seconds"] == 10
    assert Path(ref["path"]).is_relative_to(tmp_path / "CAD — owner's files")


def test_rejects_symlink_snapshot_without_reading_or_removing_target(store, tmp_path):
    ref = store.publish(_body())
    path = Path(ref["path"])
    target = tmp_path / "outside.json"
    path.replace(target)
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("Symlinks unavailable")
    before = target.read_bytes()
    with pytest.raises(SnapshotUnavailable, match="symlink"):
        store.read(ref["id"])
    assert target.read_bytes() == before and path.is_symlink()


def test_corrupt_snapshot_counts_toward_quota_and_never_returns_data(store):
    ref = store.publish(_body())
    path = Path(ref["path"])
    path.write_text("{}")
    with pytest.raises((ValueError, KeyError)):
        store.read(ref["id"])
    path.write_bytes(b"x" * 4000)
    store.max_bytes = 2000
    store.publish(_body("new"))
    assert not path.exists() and sum(p.stat().st_size for p in _files(store)) <= 2000


@pytest.mark.parametrize("corrupt", ["[]", "null", "42", "invalid JSON", json.dumps({"snapshot_retention": {"created_at": 2 ** 2000}})])
def test_corrupt_json_is_unavailable_and_does_not_poison_other_publications(store, corrupt):
    ref = store.publish(_body())
    Path(ref["path"]).write_text(corrupt)
    with pytest.raises(SnapshotUnavailable, match="integrity"):
        store.read(ref["id"])
    store.publish(_body("still usable"))


def test_read_protects_older_requested_snapshot_during_quota_reduction(store, monkeypatch):
    first = store.publish(_body("a"))
    monkeypatch.setattr("agentcad.inspection_snapshots._now", lambda: 1001)
    second = store.publish(_body("b"))
    store.max_bytes = Path(first["path"]).stat().st_size
    body, _ = store.read(first["id"])
    assert body["payload"]["value"] == "a"
    assert Path(first["path"]).exists() and not Path(second["path"]).exists()


def test_killed_writer_temps_are_removed_and_unrelated_files_survive(store):
    ref = store.publish(_body())
    orphan = store.directory / ("." + ref["id"] + ".json.deadwriter.tmp")
    orphan.write_bytes(b"unpublished partial response")
    unrelated = store.directory / "notes.tmp"
    unrelated.write_bytes(b"unrelated")
    store.read(ref["id"])
    assert not orphan.exists() and unrelated.read_bytes() == b"unrelated"


def _hold_reader(root, snapshot_id, ready, release, result):
    store = SnapshotStore(ProjectLayout(Path(root), Path(root)))
    original = store._load

    def paused(path):
        copied = original(path)
        ready.set()
        if not release.wait(15):
            raise RuntimeError("Parent did not release reader")
        return copied

    store._load = paused
    try:
        body, _ = store.read(snapshot_id)
        result.put(body["payload"])
    except Exception as exc:
        result.put(str(exc))


def test_process_reader_and_cleanup_share_os_lock(tmp_path):
    store = SnapshotStore(ProjectLayout(tmp_path, tmp_path))
    first = store.publish(_body("a"))
    _policy(tmp_path, max_bytes=Path(first["path"]).stat().st_size + 20)
    context = multiprocessing.get_context("spawn")
    ready, release, result = context.Event(), context.Event(), context.Queue()
    child = context.Process(target=_hold_reader, args=(str(tmp_path), first["id"], ready, release, result))
    child.start()
    try:
        assert ready.wait(15)
        with ThreadPoolExecutor(max_workers=1) as pool:
            writer = pool.submit(SnapshotStore(ProjectLayout(tmp_path, tmp_path)).publish, _body("b"))
            try:
                # The reader copied the bytes but still owns the transaction.
                with pytest.raises(TimeoutError):
                    writer.result(timeout=0.2)
                assert Path(first["path"]).exists()
            finally:
                release.set()
            second = writer.result(timeout=15)
        assert result.get(timeout=15) == _body("a")["payload"]
        assert not Path(first["path"]).exists() and Path(second["path"]).exists()
    finally:
        release.set()
        child.join(15)
        if child.is_alive():
            child.terminate()
            child.join(5)
    assert child.exitcode == 0
