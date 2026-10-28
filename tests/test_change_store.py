"""Immutable attempt storage without a CAD runtime or a promotion transaction."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agentcad.change_contract import validate_document
from agentcad.change_store import ChangeStore, ChangeStoreError

SOURCE = b"part = Box(60, 30, 6) - Cylinder(6, 12)\nshow_object(part)\n"


def initialized(root):
    (root / "agentcad.json").write_text(json.dumps({"versions": [], "current": "accepted"}))
    return ChangeStore(root)


def begin(store, **overrides):
    kwargs = dict(
        command="run",
        label="bracket",
        source=SOURCE,
        source_name="bracket.py",
        model={"runtime": "build123d", "params": ""},
    )
    kwargs.update(overrides)
    return store.begin(**kwargs)


def test_attempt_retains_source_before_work_and_survives_a_new_store(tmp_path):
    store = initialized(tmp_path)
    attempt = begin(store)
    assert attempt.source_path.read_bytes() == SOURCE
    pending = ChangeStore(tmp_path).read(attempt.revision_id)
    assert pending["status"] == "in_progress"
    assert pending["request"]["parent_revision_id"] is None
    assert pending["candidate"] is None
    assert json.loads((tmp_path / "agentcad.json").read_text())["current"] == "accepted"


def test_failure_without_geometry_retains_candidate_and_diagnostics(tmp_path):
    store = initialized(tmp_path)
    attempt = begin(store)
    report = {
        "command": "run",
        "status": "failed",
        "error": "boom",
        "script_output": "before failure",
    }
    reference = attempt.finish(report)
    report["error"] = "later mutation"
    loaded = ChangeStore(tmp_path).read(attempt.revision_id)
    assert loaded["status"] == "completed"
    assert loaded["outcome"]["response"]["error"] == "boom"
    assert loaded["outcome"]["response"]["script_output"] == "before failure"
    assert loaded["candidate"]["deliverables"]["cad"] == []
    assert loaded["receipt"]["promotion_ready"] is False
    assert loaded["receipt"]["validation_state"] == "invalid"
    assert reference["revision_id"] == attempt.revision_id
    assert Path(reference["source"]).read_bytes() == SOURCE
    assert reference["geometry"] is None
    validate_document(loaded["candidate"])
    validate_document(loaded["receipt"])


def test_available_geometry_is_pinned_even_for_an_invalid_result(tmp_path):
    store = initialized(tmp_path)
    attempt = begin(store)
    emitted = tmp_path / "staged.step"
    emitted.write_bytes(b"diagnostic STEP bytes")
    attempt.prepare(
        {"status": "invalid_geometry", "validation": {"is_valid": False}}, geometry=emitted
    )
    emitted.write_bytes(b"replacement")
    reference = attempt.finish({"command": "run", "status": "invalid_geometry"})
    loaded = ChangeStore(tmp_path).read(attempt.revision_id)
    assert Path(reference["geometry"]).read_bytes() == b"diagnostic STEP bytes"
    assert loaded["receipt"]["validation_state"] == "invalid"
    assert loaded["receipt"]["candidate_retained"] is True
    assert json.loads((tmp_path / "agentcad.json").read_text())["current"] == "accepted"


def test_prepared_core_survives_interruption_before_the_final_response(tmp_path):
    store = initialized(tmp_path)
    attempt = begin(store)
    step = tmp_path / "output.step"
    step.write_bytes(b"written STEP")
    attempt.prepare({"status": "success", "validation": {"is_valid": True}}, geometry=step)
    loaded = ChangeStore(tmp_path).read(attempt.revision_id)
    assert loaded["status"] == "in_progress"
    assert loaded["candidate"] is not None
    assert loaded["receipt"]["validation_state"] == "valid"
    assert loaded["outcome"] is None


def test_repair_has_parentage_and_keeps_failed_attempt_unchanged(tmp_path):
    store = initialized(tmp_path)
    failed = begin(store)
    failed.finish({"status": "failed", "error": "boom"})
    original = copy.deepcopy(store.read(failed.revision_id))
    repaired = begin(store, source=SOURCE + b"# repaired\n", label="fixed")
    repaired.finish({"status": "failed", "error": "still needs geometry"})
    assert store.read(repaired.revision_id)["request"]["parent_revision_id"] == failed.revision_id
    assert store.read(failed.revision_id) == original
    assert {entry["revision_id"] for entry in store.list_attempts()} == {
        failed.revision_id,
        repaired.revision_id,
    }


def test_concurrent_attempts_have_unique_ids_and_do_not_lose_the_working_selection(tmp_path):
    store = initialized(tmp_path)
    with ThreadPoolExecutor(max_workers=6) as pool:
        attempts = list(pool.map(lambda i: begin(store, label=f"part-{i}"), range(12)))
    assert len({a.revision_id for a in attempts}) == 12
    requests = [store.read(a.revision_id)["request"] for a in attempts]
    generations = sorted(r["selection_generation"] for r in requests)
    assert generations == list(range(1, 13))
    newest = max(requests, key=lambda r: r["selection_generation"])
    oldest = min(requests, key=lambda r: r["selection_generation"])
    attempts_by_id = {a.revision_id: a for a in attempts}
    attempts_by_id[oldest["revision_id"]].finish({"status": "failed", "error": "late failure"})
    assert store.working_revision_id == newest["revision_id"]


def test_finished_attempts_are_not_overwritten_and_corruption_is_not_hidden(tmp_path):
    store = initialized(tmp_path)
    attempt = begin(store)
    ref = attempt.finish({"status": "failed", "error": "first"})
    assert attempt.finish({"status": "failed", "error": "first"}) == ref
    with pytest.raises(ChangeStoreError):
        attempt.finish({"status": "success"})
    attempt.source_path.write_bytes(b"tampered")
    with pytest.raises(ChangeStoreError):
        ChangeStore(tmp_path).read(attempt.revision_id)


@pytest.mark.parametrize("revision", ["../outside", "/outside", "r_wrong", ""])
def test_lookup_rejects_unsafe_or_unknown_revision_ids(tmp_path, revision):
    store = initialized(tmp_path)
    with pytest.raises(ChangeStoreError):
        store.read(revision)


def test_store_does_not_initialize_a_project_or_follow_an_escaping_symlink(tmp_path):
    with pytest.raises(ChangeStoreError):
        begin(ChangeStore(tmp_path))
    assert not (tmp_path / ".agentcad").exists()
    initialized(tmp_path)
    elsewhere = tmp_path / "outside"
    elsewhere.mkdir()
    (tmp_path / ".agentcad").mkdir()
    (tmp_path / ".agentcad" / "changes").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ChangeStoreError):
        begin(ChangeStore(tmp_path))
    assert list(elsewhere.iterdir()) == []


def test_two_identical_labels_still_have_distinct_immutable_revisions(tmp_path):
    store = initialized(tmp_path)
    first, second = begin(store), begin(store)
    assert first.revision_id != second.revision_id
    assert first.source_path != second.source_path


def test_geometry_written_before_validation_failure_is_still_retained(tmp_path):
    store = initialized(tmp_path)
    attempt = begin(store)
    step = tmp_path / "partial.step"
    step.write_bytes(b"emitted but not validated")
    attempt.capture_geometry(step)
    step.unlink()
    ref = attempt.finish({"status": "error", "message": "validator crashed"})
    assert Path(ref["geometry"]).read_bytes() == b"emitted but not validated"
    assert store.read(attempt.revision_id)["receipt"]["promotion_ready"] is False


def test_staged_storage_does_not_accept_or_modify_legacy_history(tmp_path):
    store = initialized(tmp_path)
    attempt = begin(store, promotion_mode="staged")
    step = tmp_path / "result.step"
    step.write_bytes(b"valid result")
    attempt.prepare({"status": "success", "validation": {"is_valid": True}}, geometry=step)
    attempt.finish({"status": "success"})
    assert store.read(attempt.revision_id)["candidate"]["promotion_mode"] == "staged"
    assert json.loads((tmp_path / "agentcad.json").read_text())["current"] == "accepted"


def test_declared_optional_artifacts_are_retained_and_verified_separately_from_core(tmp_path):
    store = initialized(tmp_path)
    attempt = begin(store)
    output = tmp_path / "v1_bracket"
    output.mkdir()
    manifest = output / "geometry_manifest.json"
    manifest.write_text('{"manifest_id":"stable"}')
    image = output / "preview.png"
    image.write_bytes(b"preview bytes")
    response = {
        "status": "success",
        "outputs": {"geometry_manifest": "v1_bracket/geometry_manifest.json"},
        "preview": "v1_bracket/preview.png",
    }
    attempt.finish(response)
    saved = store.read(attempt.revision_id)
    refs = {a["name"]: a for a in saved["outcome"]["artifacts"]}
    assert set(refs) == {"outputs.geometry_manifest", "preview"}
    manifest.write_text("new identity")
    image.unlink()
    assert (
        tmp_path / refs["outputs.geometry_manifest"]["path"]
    ).read_text() == '{"manifest_id":"stable"}'
    # Replaying the completed response does not recapture changed live files.
    attempt.finish(response)
    assert store.read(attempt.revision_id) == saved
    (tmp_path / refs["outputs.geometry_manifest"]["path"]).write_text("corrupt snapshot")
    with pytest.raises(ChangeStoreError):
        store.read(attempt.revision_id)


def test_artifact_retention_never_reads_a_declared_external_file(tmp_path):
    store = initialized(tmp_path)
    attempt = begin(store)
    outside = tmp_path.parent / (tmp_path.name + "-external.txt")
    outside.write_text("must not copy")
    attempt.finish({"status": "error", "outputs": {"unexpected": str(outside)}})
    saved = store.read(attempt.revision_id)
    assert saved["outcome"]["artifacts"] == []
    assert saved["outcome"]["unavailable_artifacts"][0]["name"] == "outputs.unexpected"


def test_per_part_and_comparison_review_artifacts_are_retained(tmp_path):
    store = initialized(tmp_path)
    attempt = begin(store)
    image = tmp_path / "pin.png"
    image.write_bytes(b"part preview")
    overlay = tmp_path / "overlay.png"
    overlay.write_bytes(b"comparison overlay")
    attempt.finish(
        {
            "status": "success",
            "parts": [{"name": "pin", "preview": "pin.png"}],
            "diff": {"overlay": "overlay.png", "status": "success", "previous": "old"},
        }
    )
    saved = store.read(attempt.revision_id)
    refs = {a["name"]: a for a in saved["outcome"]["artifacts"]}
    assert set(refs) == {"parts.0.preview", "diff.overlay"}
    assert saved["outcome"]["unavailable_artifacts"] == []
    image.unlink()
    overlay.unlink()
    assert (tmp_path / refs["parts.0.preview"]["path"]).read_bytes() == b"part preview"


def test_inspection_catalogue_retains_media_even_when_inline_report_is_disabled(tmp_path):
    store = initialized(tmp_path)
    attempt = begin(store)
    image = tmp_path / "inspection" / "face.png"
    image.parent.mkdir()
    image.write_bytes(b"labeled inspection image")
    image.with_suffix(".json").write_text('{"target_id":"face:f1"}')
    bundle = tmp_path / "inspection" / "bundle.json"
    bundle.write_text(json.dumps({"media": [{"id": "face:f1", "png": "inspection/face.png"}]}))
    attempt.finish(
        {"status": "success", "outputs": {"inspection_bundle": "inspection/bundle.json"}}
    )
    refs = {a["name"]: a for a in store.read(attempt.revision_id)["outcome"]["artifacts"]}
    assert {
        "outputs.inspection_bundle",
        "inspection.media.0.png",
        "inspection.media.0.metadata",
    } == set(refs)
    image.unlink()
    assert (
        tmp_path / refs["inspection.media.0.png"]["path"]
    ).read_bytes() == b"labeled inspection image"


def test_corrupted_working_selection_is_not_silently_replaced(tmp_path):
    store = initialized(tmp_path)
    first = begin(store)
    state = tmp_path / ".agentcad/changes/working.json"
    state.write_text("[]")
    with pytest.raises(ChangeStoreError):
        begin(store)
    assert first.source_path.read_bytes() == SOURCE
