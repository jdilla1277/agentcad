"""Measured cache groups, job pins and byte/age limits without native CAD."""

import json
from pathlib import Path

import pytest

from agentcad.geometry_manifest import _digest
from agentcad.inspection_cache import cache_lease, CacheResourceLimit, CacheUnavailable
from agentcad.project import ProjectLayout
from agentcad.versioning import atomic_write_json


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr("agentcad.inspection_cache._now", lambda: 1000)
    return ProjectLayout(tmp_path, tmp_path), tmp_path / "v1_part"


def _report(root, number=1):
    return root / "inspection" / f"report_{number:016x}.json"


def _config(layout, **settings):
    (layout.project_root / "agentcad.toml").write_text('[inspection.cache]\n' + '\n'.join(f'{key} = {json.dumps(value)}' for key, value in settings.items()))


def _job(root, paths, status="stopped"):
    directory = root / "inspection/jobs/inspection_test"
    body = {"schema_version": 1, "job_id": directory.name, "status": status,
            "stages": {"analysis": {"status": "complete", "artifacts": [{"path": str(p), "sha256": "original"} for p in paths]}}}
    atomic_write_json(directory / "state.json", {**body, "checksum": _digest(body)})
    return directory, body


def test_age_does_not_renew_and_fresh_publication_gets_new_lifetime(project, monkeypatch):
    layout, root = project
    _config(layout, max_age_seconds=10)
    path = _report(root)
    with cache_lease(root, layout=layout) as cache:
        cache.publish_json(path, {"original": True})
        before = cache.reference()
    original = path.read_bytes()
    monkeypatch.setattr("agentcad.inspection_cache._now", lambda: 1009)
    with cache_lease(root, layout=layout, protect=[path]) as cache:
        cache.validate([path])
        assert cache.reference()["expires_at"] == before["expires_at"]
        assert path.read_bytes() == original
    monkeypatch.setattr("agentcad.inspection_cache._now", lambda: 1010)
    with cache_lease(root, layout=layout):
        assert not path.exists()
    with cache_lease(root, layout=layout) as cache:
        cache.publish_json(path, {"original": True})
        assert cache.reference()["expires_at"] > before["expires_at"]


def test_global_quota_counts_other_version_caches_and_evicts_oldest(project, monkeypatch):
    layout, root = project
    other = layout.build_root / "v2_other"
    with cache_lease(root, layout=layout) as cache:
        cache.publish_json(_report(root), {"data": "x" * 1000})
    size = _report(root).stat().st_size
    _config(layout, max_bytes=size + 10)
    monkeypatch.setattr("agentcad.inspection_cache._now", lambda: 1001)
    with cache_lease(other, layout=layout) as cache:
        cache.publish_json(_report(other), {"data": "y" * 1000})
    assert not _report(root).exists() and _report(other).exists()


def test_resumable_checkpoints_survive_age_and_quota_and_release_once(project, monkeypatch):
    layout, root = project
    path = _report(root)
    with cache_lease(root, layout=layout) as cache:
        cache.publish_json(path, {"data": "x" * 1000})
    directory, state = _job(root, [path])
    before = path.read_bytes()
    _config(layout, max_age_seconds=1, max_bytes=2000)
    monkeypatch.setattr("agentcad.inspection_cache._now", lambda: 2000)
    with cache_lease(root, layout=layout, protect=[path]) as cache:
        assert path.exists()
        assert cache.reference()["state"] == "protected"
        assert cache.reference()["expires_at"] is None
        assert cache.reference()["protected_bytes"] > len(before)
        with pytest.raises(CacheResourceLimit, match="resume"):
            cache.publish_json(_report(root, 2), {"data": "z" * 2000})
    assert path.read_bytes() == before
    state["status"] = "complete"
    state["completed_at"] = 2000
    atomic_write_json(directory / "state.json", {**state, "checksum": _digest(state)})
    with cache_lease(root, layout=layout, protect=[path]) as cache:
        released = cache.reference()
        assert released["state"] == "retained" and released["protected_bytes"] == 0
    monkeypatch.setattr("agentcad.inspection_cache._now", lambda: 2001)
    with cache_lease(root, layout=layout):
        assert not path.exists()


@pytest.mark.parametrize("status", ["running", "queued", "stopped", "failed", "canceled", "resource_limit"])
def test_every_resumable_job_state_protects_checkpoints(project, status):
    layout, root = project
    path = _report(root)
    with cache_lease(root, layout=layout) as cache:
        cache.publish_json(path, {"fact": "retained"})
    _job(root, [path], status)
    _config(layout, max_bytes=10)
    with pytest.raises(CacheResourceLimit):
        with cache_lease(root, layout=layout):
            pass
    assert path.is_file()


def test_png_and_sidecar_evicted_together_and_unused_files_survive(project):
    layout, root = project
    png = root / "inspection/face_v1_1_highlighted_123456789abcdef0.png"
    temp = layout.build_root / "rendered.png"
    temp.write_bytes(b"PNG bytes")
    unrelated = root / "inspection/geometry_manifest.json"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"durable ID evidence")
    with cache_lease(root, layout=layout) as cache:
        cache.publish_image(temp, png, png.with_suffix(".json"), {"png_sha256": "hash"})
    _config(layout, max_bytes=20)
    with cache_lease(root, layout=layout):
        pass
    assert not png.exists() and not png.with_suffix(".json").exists()
    assert unrelated.read_bytes() == b"durable ID evidence"


def test_legacy_cache_adoption_does_not_change_original_bytes(project):
    layout, root = project
    path = _report(root)
    atomic_write_json(path, {"legacy": "evidence"})
    before = path.read_bytes()
    with cache_lease(root, layout=layout, protect=[path]) as cache:
        cache.validate([path])
        assert cache.reference()["state"] == "retained"
    assert path.read_bytes() == before


def test_corrupt_retention_index_fails_closed(project):
    layout, root = project
    with cache_lease(root, layout=layout) as cache:
        cache.publish_json(_report(root), {"original": True})
        index = cache.index_path
    index.write_text('{}')
    before = _report(root).read_bytes()
    with pytest.raises(CacheUnavailable, match="restore"):
        with cache_lease(root, layout=layout):
            pass
    assert _report(root).read_bytes() == before


@pytest.mark.parametrize("settings", [{"max_bytes": 0}, {"max_age_seconds": -1}, {"max_bytes": True}, {"typo": 1}])
def test_invalid_cache_policy_does_not_publish(project, settings):
    layout, root = project
    _config(layout, **settings)
    with pytest.raises(CacheUnavailable, match="inspection.cache"):
        with cache_lease(root, layout=layout):
            pass
    assert not _report(root).exists()


def test_external_standalone_cache_is_explicitly_unmanaged(project, tmp_path):
    layout, root = project
    outside = tmp_path.parent / (tmp_path.name + '_external')
    with cache_lease(outside, layout=layout) as cache:
        cache.publish_json(_report(outside), {"external": True})
        assert cache.reference()["state"] == "unmanaged"
    assert not (layout.build_root / '.agentcad/inspection_cache/index.json').exists()


def test_existing_capture_deadline_cannot_be_extended_by_regeneration(project, monkeypatch):
    layout, root = project
    _config(layout, max_age_seconds=1)
    path = _report(root)
    with cache_lease(root, layout=layout) as cache:
        cache.publish_json(path, {"old": True})
        old = cache.reference()
    monkeypatch.setattr('agentcad.inspection_cache._now', lambda: 1001)
    with cache_lease(root, layout=layout) as cache:
        cache.publish_json(path, {"old": True})
        with pytest.raises(CacheUnavailable, match="expired"):
            cache.validate([path], old)


def test_oversized_publication_keeps_other_retained_groups(project):
    layout, root = project
    with cache_lease(root, layout=layout) as cache:
        cache.publish_json(_report(root), {"old": "evidence"})
    before = _report(root).read_bytes()
    _config(layout, max_bytes=1000)
    with cache_lease(root, layout=layout) as cache:
        with pytest.raises(CacheResourceLimit):
            cache.publish_json(_report(root, 2), {"big": "x" * 2000})
    assert _report(root).read_bytes() == before


def test_protected_corrupt_artifact_is_not_replaced(project):
    layout, root = project
    path = _report(root)
    with cache_lease(root, layout=layout) as cache:
        cache.publish_json(path, {"original": True})
    _job(root, [path])
    path.write_bytes(b"corrupted retained checkpoint")
    with cache_lease(root, layout=layout) as cache:
        with pytest.raises(CacheUnavailable, match="restore"):
            cache.publish_json(path, {"original": True})
    assert path.read_bytes() == b"corrupted retained checkpoint"


def test_abandoned_scratch_is_removed_without_touching_foreign_names(project):
    layout, root = project
    directory = root / 'inspection'
    orphan = directory / '.render-dead/capture'
    orphan.mkdir(parents=True)
    (orphan / 'iso.png').write_bytes(b"incomplete render")
    temporary = directory / '.report_123456789abcdef0.json.dead.tmp'
    temporary.write_bytes(b"incomplete JSON")
    unrelated = directory / 'keep-me.tmp'
    unrelated.write_bytes(b"foreign file")
    with cache_lease(root, layout=layout):
        pass
    assert not orphan.exists() and not temporary.exists()
    assert unrelated.read_bytes() == b"foreign file"


def _reader(root, ready, release, result):
    from agentcad.inspection_cache import cache_lease
    from agentcad.project import ProjectLayout
    layout = ProjectLayout(Path(root), Path(root))
    path = _report(Path(root) / 'v1_part')
    with cache_lease(path.parent.parent, layout=layout, protect=[path]) as cache:
        cache.validate([path])
        copied = path.read_bytes()
        ready.set()
        if not release.wait(15):
            raise RuntimeError('Reader was not released')
        result.put(copied)


def test_process_read_blocks_cleanup_until_bytes_are_copied(tmp_path):
    import multiprocessing
    from concurrent.futures import ThreadPoolExecutor
    layout, root = ProjectLayout(tmp_path, tmp_path), tmp_path / 'v1_part'
    with cache_lease(root, layout=layout) as cache:
        cache.publish_json(_report(root), {"reader": "original"})
    original = _report(root).read_bytes()
    _config(layout, max_bytes=len(original) + 4)
    context = multiprocessing.get_context('spawn')
    ready, release, result = context.Event(), context.Event(), context.Queue()
    child = context.Process(target=_reader, args=(str(tmp_path), ready, release, result))
    child.start()

    def publish_other():
        other = tmp_path / 'v2_other'
        with cache_lease(other, layout=layout) as cache:
            cache.publish_json(_report(other), {"writer": "new"})

    try:
        assert ready.wait(15)
        with ThreadPoolExecutor(max_workers=1) as pool:
            writer = pool.submit(publish_other)
            try:
                with pytest.raises(TimeoutError):
                    writer.result(timeout=0.2)
                assert _report(root).read_bytes() == original
            finally:
                release.set()
            writer.result(timeout=15)
        assert result.get(timeout=15) == original
        assert not _report(root).exists()
    finally:
        release.set()
        child.join(15)
        if child.is_alive():
            child.terminate()
            child.join(5)
    assert child.exitcode == 0


def test_unmanaged_source_writer_coordinates_with_its_managed_reader(tmp_path):
    import multiprocessing
    from concurrent.futures import ThreadPoolExecutor
    layout, root = ProjectLayout(tmp_path, tmp_path), tmp_path / 'v1_part'
    with cache_lease(root, layout=layout) as cache:
        cache.publish_json(_report(root), {"reader": "original"})
    original = _report(root).read_bytes()
    foreign = tmp_path.parent / (tmp_path.name + '_caller')
    foreign.mkdir()
    other_layout = ProjectLayout(foreign, foreign)
    context = multiprocessing.get_context('spawn')
    ready, release, result = context.Event(), context.Event(), context.Queue()
    child = context.Process(target=_reader, args=(str(tmp_path), ready, release, result))
    child.start()

    def source_side_write():
        with cache_lease(root, layout=other_layout) as cache:
            assert not cache.managed
            cache.publish_json(_report(root, 2), {"source_side": True})

    try:
        assert ready.wait(15)
        with ThreadPoolExecutor(max_workers=1) as pool:
            writer = pool.submit(source_side_write)
            try:
                with pytest.raises(TimeoutError):
                    writer.result(timeout=0.2)
            finally:
                release.set()
            writer.result(timeout=15)
        assert result.get(timeout=15) == original
        assert _report(root).read_bytes() == original and _report(root, 2).is_file()
    finally:
        release.set()
        child.join(15)
        if child.is_alive():
            child.terminate()
            child.join(5)
    assert child.exitcode == 0


def test_killed_index_publication_is_reclaimed_without_changing_artifacts(project):
    layout, root = project
    with cache_lease(root, layout=layout) as cache:
        cache.publish_json(_report(root), {"original": "retained"})
        directory = cache.directory
    original = _report(root).read_bytes()
    orphan = directory / '.index.json.killed.tmp'
    orphan.write_bytes(b'partial unpublished index')
    unrelated = directory / 'notes.tmp'
    unrelated.write_bytes(b'unrelated')
    with cache_lease(root, layout=layout, protect=[_report(root)]) as cache:
        cache.validate([_report(root)])
    assert not orphan.exists() and unrelated.read_bytes() == b'unrelated'
    assert _report(root).read_bytes() == original


def test_delayed_first_read_does_not_renew_a_completed_jobs_lifetime(project, monkeypatch):
    layout, root = project
    _config(layout, max_age_seconds=10)
    path = _report(root)
    with cache_lease(root, layout=layout) as cache:
        cache.publish_json(path, {"original": True})
    directory, state = _job(root, [path])
    with cache_lease(root, layout=layout, protect=[path]) as cache:
        assert cache.reference()["state"] == "protected"
    state.update(status="complete", completed_at=1010)
    atomic_write_json(directory / 'state.json', {**state, 'checksum': _digest(state)})
    # The worker might die after committing completion but before refreshing
    # the cache index. A later reader must use the original completion time.
    monkeypatch.setattr('agentcad.inspection_cache._now', lambda: 5000)
    with cache_lease(root, layout=layout):
        assert not path.exists()
