"""Bounded saved-byte retrieval without CAD imports or file-system escape."""

from tests.inspection_helpers import experimental_args
import base64
import hashlib
import json
from pathlib import Path
import shlex

import pytest

from agentcad.cli import cli
from agentcad.geometry_manifest import _digest
from agentcad.inspection_artifacts import ArtifactUnavailable, MAX_CHUNK_BYTES, read_artifact


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def evidence(isolated_dir):
    step = isolated_dir / "v1_sample" / "output.step"
    step.parent.mkdir()
    step.write_bytes(b"saved baseline bytes" * 500)
    body = {"schema_version": 1, "version": 1, "baseline": {"file": step.name,
            "sha256": hashlib.sha256(step.read_bytes()).hexdigest()}, "entities": {}, "counts": {}}
    manifest = {**body, "manifest_id": _digest(body)}
    write_json(step.with_name("geometry_manifest.json"), manifest)
    return step, manifest


def read(evidence, artifact, **kwargs):
    step, _ = evidence
    return read_artifact(step, artifact, step.parent, **kwargs)


def test_saved_bytes_complete_hash_and_pinned_cli_continuation(evidence, runner, monkeypatch):
    step, saved = evidence
    before = {p: p.read_bytes() for p in step.parent.iterdir()}
    first = read(evidence, "baseline", length=17)
    assert first["manifest_id"] == saved["manifest_id"]
    assert first["artifact"]["returned_bytes"] == 17
    assert not first["artifact"]["complete"]
    args = shlex.split(first["next"]["cli"])[1:]
    child = step.parent / "caller"
    child.mkdir()
    monkeypatch.chdir(child)
    result = runner.invoke(cli, experimental_args([*args, "--no-daemon"]))
    assert result.exit_code == 0, result.stdout
    second = json.loads(result.stdout)
    assert second["artifact"]["offset"] == 17
    assert base64.b64decode(second["artifact"]["data"]) == step.read_bytes()[17:34]
    assert second["artifact"]["sha256"] == first["artifact"]["sha256"]
    assert {p: p.read_bytes() for p in before} == before
    assert not (step.parent / "inspection").exists()  # no cache or native work


def test_manifest_and_exact_baseline_reference(evidence):
    step, _ = evidence
    for ref in ("manifest", "geometry_manifest.json", str(step.with_name("geometry_manifest.json")), "baseline", str(step)):
        result = read(evidence, ref, length=MAX_CHUNK_BYTES)
        expected = step if ref in {"baseline", str(step)} else step.with_name("geometry_manifest.json")
        raw = base64.b64decode(result["artifact"]["data"])
        assert raw == expected.read_bytes()
        assert result["artifact"]["sha256"] == hashlib.sha256(raw).hexdigest()
        assert result["artifact"]["retention"] == {"state": "durable"}
        assert result["artifact"]["complete"] and result["next"] is None


@pytest.mark.parametrize("kwargs", [
    {"offset": -1}, {"offset": True}, {"length": 0}, {"length": MAX_CHUNK_BYTES + 1},
    {"length": False}, {"offset": 1}, {"expected_sha256": "z" * 64},
    {"expected_sha256": "0" * 64}, {"offset": 10000, "expected_sha256": "0" * 64},
])
def test_invalid_chunk_or_unpinned_continuation(evidence, kwargs):
    with pytest.raises(ArtifactUnavailable):
        read(evidence, "baseline", **kwargs)


@pytest.mark.parametrize("ref", ["../output.step", "inspection/../../secret", "agentcad.toml", "meta.json", "edit.py", "https://example.test/x", "", "x\x00y", "x\\y"])
def test_only_advertised_artifact_types_are_readable(evidence, ref):
    with pytest.raises(ArtifactUnavailable):
        read(evidence, ref)


def test_wrong_version_and_other_directory(evidence, isolated_dir):
    step, _ = evidence
    foreign = isolated_dir / "v2_other" / "geometry_manifest.json"
    write_json(foreign, {"secret": "never returned"})
    with pytest.raises(ArtifactUnavailable):
        read(evidence, str(foreign))
    with pytest.raises(ArtifactUnavailable):
        read(evidence, str(foreign.parent / "inspection" / ("report_" + "a" * 16 + ".json")))


def test_changed_manifest_or_baseline_never_returns_bytes(evidence):
    step, _ = evidence
    original = step.read_bytes()
    step.write_bytes(original + b"changed")
    with pytest.raises(ArtifactUnavailable, match="Baseline changed"):
        read(evidence, "manifest")
    step.write_bytes(original)
    manifest = step.with_name("geometry_manifest.json")
    data = json.loads(manifest.read_text())
    data["version"] = 2
    write_json(manifest, data)
    with pytest.raises(ArtifactUnavailable, match="manifest integrity"):
        read(evidence, "baseline")


@pytest.mark.skipif(__import__("os").name == "nt", reason="symlink privileges vary on Windows")
def test_symlink_file_and_intermediate_cache_directory(evidence, isolated_dir):
    step, _ = evidence
    outside = isolated_dir / "private"
    outside.mkdir()
    name = "report_" + "a" * 16 + ".json"
    write_json(outside / name, {"secret": "not inspection evidence"})
    (step.parent / "inspection").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ArtifactUnavailable, match="symlink"):
        read(evidence, str(step.parent / "inspection" / name))
    manifest = step.with_name("geometry_manifest.json")
    old = manifest.read_bytes()
    manifest.unlink()
    target = outside / "manifest.json"
    target.write_bytes(old)
    manifest.symlink_to(target)
    with pytest.raises(ArtifactUnavailable, match="symlink"):
        read(evidence, "manifest")


def report(evidence):
    step, saved = evidence
    data = {"analysis_revision": "a" * 64, "manifest_id": saved["manifest_id"],
            "baseline_sha256": saved["baseline"]["sha256"], "parts": ["all parts"] * 100}
    return write_json(step.parent / "inspection" / ("report_" + "a" * 16 + ".json"), {**data, "sha256": _digest(data)})


def test_report_integrity_change_between_chunks_and_no_regeneration(evidence):
    path = report(evidence)
    first = read(evidence, str(path), length=20)
    # Even a validly checksummed new body cannot replace the first response.
    data = json.loads(path.read_text())
    data.pop("sha256")
    data["parts"].append("new content")
    write_json(path, {**data, "sha256": _digest(data)})
    with pytest.raises(ArtifactUnavailable, match="changed since the first chunk"):
        read(evidence, str(path), offset=20, expected_sha256=first["artifact"]["sha256"],
             expected_expires_at=first["artifact"]["retention"]["expires_at"])
    path.unlink()
    with pytest.raises(ArtifactUnavailable, match="missing, expired or evicted"):
        read(evidence, str(path))
    assert not path.exists()


def test_wrong_baseline_report(evidence):
    path = report(evidence)
    data = json.loads(path.read_text())
    data.pop("sha256")
    data["baseline_sha256"] = "0" * 64
    write_json(path, {**data, "sha256": _digest(data)})
    with pytest.raises(ArtifactUnavailable, match="another geometry"):
        read(evidence, str(path))


@pytest.mark.parametrize("options", [
    ["--artifact-offset", "1"], ["--artifact-bytes", "50"], ["--artifact-sha256", "a" * 64],
    ["--artifact", "manifest", "--report"], ["--artifact", "manifest", "--id", "face_v1_1"],
    ["--artifact", "manifest", "--image"], ["--artifact", "manifest", "--response-text-budget", "2000"],
])
def test_cli_rejects_conflicting_or_orphaned_controls(evidence, runner, options):
    result = runner.invoke(cli, experimental_args(["inspect", str(evidence[0]), *options, "--no-daemon"]))
    assert result.exit_code != 0
    data = json.loads(result.stdout)
    assert data["status"] == "error" and "artifact" not in data


def test_metadata_validation_is_bounded(evidence, monkeypatch):
    path = report(evidence)
    monkeypatch.setattr("agentcad.inspection_artifacts.MAX_JSON_BYTES", 100)
    with pytest.raises(ArtifactUnavailable, match="32 MiB"):
        read(evidence, str(path))


def test_expiry_quota_and_idle_continuation_do_not_pin_evidence(evidence, isolated_dir, monkeypatch):
    from agentcad.inspection_cache import cache_lease, CacheUnavailable, CacheResourceLimit
    step, _ = evidence
    config = isolated_dir / "agentcad.toml"
    config.write_text('[inspection.cache]\nmax_age_seconds=10\n', encoding="utf-8")
    monkeypatch.setattr("agentcad.inspection_cache._now", lambda: 1000)
    path = report(evidence)
    data = json.loads(path.read_text())
    path.unlink()
    with cache_lease(step.parent) as cache:
        cache.publish_json(path, data)
    first = read(evidence, str(path), length=10)
    expiry = first["artifact"]["retention"]["expires_at"]
    monkeypatch.setattr("agentcad.inspection_cache._now", lambda: 1009)
    assert read(evidence, str(path))["artifact"]["retention"]["expires_at"] == expiry
    config.write_text('[inspection.cache]\nmax_age_seconds=10\nmax_bytes=1\n', encoding="utf-8")
    with pytest.raises(CacheResourceLimit):
        read(evidence, str(path))
    assert path.exists()  # the active read is protected, not evicted for itself
    assert read(evidence, "manifest")["status"] == "success"
    config.write_text('[inspection.cache]\nmax_age_seconds=10\n', encoding="utf-8")
    monkeypatch.setattr("agentcad.inspection_cache._now", lambda: 1010)
    with pytest.raises(CacheUnavailable, match="expired"):
        read(evidence, str(path), offset=10, expected_sha256=first["artifact"]["sha256"], expected_expires_at=expiry)
    with cache_lease(step.parent):
        assert not path.exists()  # an idle continuation is not a job checkpoint
    with cache_lease(step.parent) as cache:
        cache.publish_json(path, data)
    with pytest.raises(CacheUnavailable, match="expired"):
        read(evidence, str(path), offset=10, expected_sha256=first["artifact"]["sha256"], expected_expires_at=expiry)
    fresh = read(evidence, str(path), length=10)
    assert fresh["artifact"]["sha256"] == first["artifact"]["sha256"]
    assert fresh["artifact"]["retention"]["expires_at"] != expiry


def test_image_pixels_and_camera_evidence_are_bound_to_manifest(evidence):
    step, saved = evidence
    request = {"manifest_id": saved["manifest_id"], "baseline_sha256": saved["baseline"]["sha256"],
               "object_id": "face_v1_1", "mode": "isolated"}
    png = step.parent / "inspection" / f"face_v1_1_isolated_{_digest(request)[:16]}.png"
    png.parent.mkdir()
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"saved pixels" * 500)
    body = {"request": request, "png_sha256": hashlib.sha256(png.read_bytes()).hexdigest(), "projections": []}
    write_json(png.with_suffix(".json"), {**body, "metadata_sha256": _digest(body)})
    for path in (png, png.with_suffix(".json")):
        data = read(evidence, str(path), length=MAX_CHUNK_BYTES)
        assert base64.b64decode(data["artifact"]["data"]) == path.read_bytes()
    png.write_bytes(b"changed pixels")
    with pytest.raises(ArtifactUnavailable, match="saved evidence hash"):
        read(evidence, str(png))


def test_old_and_new_bundle_bindings(evidence):
    step, saved = evidence
    analysis = report(evidence)
    for binding in ({}, {"manifest_id": saved["manifest_id"], "baseline_sha256": saved["baseline"]["sha256"]}):
        body = {"analysis_revision": "a" * 64, "media": [], **binding}
        path = write_json(step.parent / "inspection" / f"bundle_{_digest(body)[:16]}.json", body)
        assert read(evidence, str(path))["status"] == "success"
    analysis.unlink()
    assert read(evidence, str(path))["status"] == "success"  # new catalogues are independently bound


def test_read_protects_file_while_copying_bytes(evidence, monkeypatch):
    from agentcad.inspection_artifacts import _read_chunk
    from agentcad.inspection_cache import _CURRENT
    step, _ = evidence
    path = report(evidence)

    def checked(file, offset, length):
        if file == path:
            lease = _CURRENT.get()
            assert lease is not None and lease.managed
            assert path.relative_to(step.parent.parent).as_posix() in lease.used
        return _read_chunk(file, offset, length)

    monkeypatch.setattr("agentcad.inspection_artifacts._read_chunk", checked)
    assert read(evidence, str(path))["status"] == "success"


def test_only_the_validated_json_bytes_can_be_returned(evidence, monkeypatch):
    from agentcad.inspection_artifacts import _read_chunk
    path = report(evidence)

    def changed_after_validation(file, offset, length):
        if file == path:
            file.write_bytes(b'{"different": "unverified bytes"}')
        return _read_chunk(file, offset, length)

    monkeypatch.setattr("agentcad.inspection_artifacts._read_chunk", changed_after_validation)
    with pytest.raises(ArtifactUnavailable, match="saved evidence hash"):
        read(evidence, str(path))


def test_external_standalone_cache_continuation_keeps_original_location(evidence, runner, isolated_dir, monkeypatch):
    step, _ = evidence
    path = report(evidence)
    caller = isolated_dir / "other caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    first = runner.invoke(cli, experimental_args(["inspect", str(step), "--artifact", str(path), "--artifact-bytes", "10", "--no-daemon"]))
    assert first.exit_code == 0, first.stdout
    payload = json.loads(first.stdout)
    assert payload["artifact"]["retention"]["state"] == "unmanaged"
    child = caller / "child"
    child.mkdir()
    monkeypatch.chdir(child)
    second = runner.invoke(cli, experimental_args([*shlex.split(payload["next"]["cli"])[1:], "--no-daemon"]))
    assert second.exit_code == 0, second.stdout
    copied = json.loads(second.stdout)
    assert copied["artifact"]["sha256"] == payload["artifact"]["sha256"]
    assert base64.b64decode(copied["artifact"]["data"]) == path.read_bytes()[10:20]
    assert not (caller / "derived").exists()  # retrieval must not create a replacement cache


def test_corrupt_image_request_returns_structured_error(evidence, runner):
    step, _ = evidence
    path = step.parent / "inspection" / ("face_v1_1_isolated_" + "a" * 16 + ".json")
    data = {"request": None, "png_sha256": "a" * 64}
    write_json(path, {**data, "metadata_sha256": _digest(data)})
    result = runner.invoke(cli, experimental_args(["inspect", str(step), "--artifact", str(path), "--no-daemon"]))
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["reason"] == "inspection_artifact_unavailable" and "artifact" not in payload
