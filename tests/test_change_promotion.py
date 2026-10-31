"""Promotion persists its decision together with the accepted version."""

import copy
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from agentcad.change_store import ChangeStore
from agentcad.versioning import atomic_write_json, reserve_version


def project(root):
    atomic_write_json(
        root / "agentcad.json",
        {
            "name": "bracket",
            "current": "accepted",
            "versions": [
                {
                    "version": 1,
                    "label": "accepted",
                    "status": "success",
                    "path": "v1_accepted/",
                }
            ],
        },
    )
    return ChangeStore(root)


def candidate(store, *, label="longer", valid=True, mode="automatic"):
    attempt = store.begin(
        command="run",
        label=label,
        source=b"exact source",
        source_name="bracket.py",
        model={"runtime": "build123d"},
        promotion_mode=mode,
    )
    step = store.root / "written.step"
    step.write_bytes(b"checked STEP: " + label.encode())
    attempt.prepare(
        {"command": "run", "status": "success", "validation": {"is_valid": valid}},
        geometry=step,
    )
    return attempt


def materialized(attempt):
    root = attempt.store.root
    request = attempt.store.read(attempt.revision_id)["request"]
    reservation = reserve_version(root, request["label"])
    (reservation.path / "output.step").write_bytes(
        attempt.directory.joinpath("geometry/output.step").read_bytes()
    )
    (reservation.path / "script.py").write_bytes(attempt.source_path.read_bytes())
    meta = {
        "command": "run",
        "status": "success",
        "version": reservation.number,
        "label": reservation.label,
        "change": {"revision_id": attempt.revision_id},
        "outputs": {"step": f"{reservation.dir_name}/output.step"},
    }
    entry = {
        "version": reservation.number,
        "label": reservation.label,
        "status": "success",
        "path": f"{reservation.dir_name}/",
    }
    return reservation, meta, entry


def promotion_store(root):
    from agentcad.change_promotion import PromotionStore

    return PromotionStore(root)


def test_request_is_durable_before_acceptance_and_receipt_replays_after_restart(
    tmp_path,
):
    store = project(tmp_path)
    attempt = candidate(store)
    promotions = promotion_store(tmp_path)
    request = promotions.begin(attempt.revision_id, request_key="agent-known-key")
    assert (
        promotion_store(tmp_path).read("agent-known-key")["result"]["lifecycle"]
        == "pending"
    )
    assert json.loads((tmp_path / "agentcad.json").read_text())["current"] == "accepted"
    reservation, meta, entry = materialized(attempt)
    receipt = promotions.commit("agent-known-key", reservation, meta, entry)
    assert receipt["outcome"] == "promoted"
    assert (
        receipt["promoted_revision_before"] is None
    )  # Legacy acceptance has no retained ID.
    assert receipt["promoted_revision_after"] == attempt.revision_id
    assert receipt["request_sha256"] == request["content_sha256"]
    assert promotions.read("agent-known-key")["accepted_version"] == entry
    assert (
        promotion_store(tmp_path).commit("agent-known-key", reservation, meta, entry)
        == receipt
    )
    manifest = json.loads((tmp_path / "agentcad.json").read_text())
    assert manifest["current"] == "longer"
    assert len(manifest["versions"]) == 2
    assert promotions.accepted("cad")["revision_id"] == attempt.revision_id
    assert meta["promotion"] == receipt


def test_key_cannot_bind_another_candidate_or_target(tmp_path):
    store = project(tmp_path)
    first = candidate(store)
    promotions = promotion_store(tmp_path)
    pinned = promotions.begin(first.revision_id, request_key="same")
    second = candidate(store, label="second")
    assert promotions.begin(first.revision_id, request_key="same") == pinned
    with pytest.raises(ValueError, match="key.*different|different.*key"):
        promotions.begin(second.revision_id, request_key="same")
    with pytest.raises(ValueError):
        promotions.begin(first.revision_id, request_key="robot", target_id="robot")
    assert promotions.accepted("robot") is None


@pytest.mark.parametrize("new_selection", ["newer", "away-and-back"])
def test_superseded_selection_cannot_be_accepted_and_refusal_is_final(
    tmp_path, new_selection
):
    store = project(tmp_path)
    first = candidate(store)
    promotions = promotion_store(tmp_path)
    promotions.begin(first.revision_id, request_key="old")
    before = json.loads((tmp_path / "agentcad.json").read_text())
    candidate(store, label="newer")
    if new_selection == "away-and-back":
        state_path = tmp_path / ".agentcad/changes/working.json"
        state = json.loads(state_path.read_text())
        state.update(
            revision_id=first.revision_id,
            selection_generation=state["selection_generation"] + 1,
        )
        atomic_write_json(state_path, state)
    reservation, meta, entry = materialized(first)
    refused = promotions.commit("old", reservation, meta, entry)
    assert refused["outcome"] == "not_promoted"
    assert "selection_changed" in refused["reason_codes"]
    assert (
        refused["operation_diagnostics"][0]["requested"]["candidate"]["revision_id"]
        == first.revision_id
    )
    after = json.loads((tmp_path / "agentcad.json").read_text())
    assert (after["current"], after["versions"]) == (
        before["current"],
        before["versions"],
    )
    assert promotions.commit("old", reservation, meta, entry) == refused
    assert not (reservation.path / "meta.json").exists()


def test_failed_check_refusal_retains_all_blockers_without_accepting(tmp_path):
    store = project(tmp_path)
    attempt = candidate(store, valid=False)
    promotions = promotion_store(tmp_path)
    promotions.begin(attempt.revision_id, request_key="bad")
    receipt = promotions.commit("bad", *materialized(attempt))
    assert receipt["outcome"] == "not_promoted"
    assert receipt["validation_state"] == "invalid"
    assert receipt["blockers"][0]["check_id"] == "written_geometry"
    assert promotions.accepted("cad") is None
    assert store.read(attempt.revision_id)["candidate"]


def test_changed_legacy_current_is_a_conflict_even_without_a_retained_target(tmp_path):
    store = project(tmp_path)
    attempt = candidate(store)
    promotions = promotion_store(tmp_path)
    promotions.begin(attempt.revision_id, request_key="guard")
    path = tmp_path / "agentcad.json"
    manifest = json.loads(path.read_text())
    manifest["versions"].append(
        {"version": 2, "label": "accepted", "status": "success", "path": "v2_accepted/"}
    )
    atomic_write_json(path, manifest)
    result = promotions.commit("guard", *materialized(attempt))
    assert result["outcome"] == "not_promoted"
    assert "accepted_target_changed" in result["reason_codes"]


def test_legacy_writer_drift_before_request_is_also_a_conflict(tmp_path):
    store = project(tmp_path)
    promotions = promotion_store(tmp_path)
    first = candidate(store)
    promotions.begin(first.revision_id, request_key="first")
    promotions.commit("first", *materialized(first))
    path = tmp_path / "agentcad.json"
    manifest = json.loads(path.read_text())
    manifest["current"] = (
        "accepted"  # A legacy writer changed current outside the target ledger.
    )
    atomic_write_json(path, manifest)
    second = candidate(store, label="second")
    promotions.begin(second.revision_id, request_key="after-drift")
    result = promotions.commit("after-drift", *materialized(second))
    assert result["outcome"] == "not_promoted"
    assert "accepted_target_changed" in result["reason_codes"]


@pytest.mark.parametrize("changed", ["retained", "delivered", "source"])
def test_changed_bytes_cannot_be_promoted(tmp_path, changed):
    store = project(tmp_path)
    attempt = candidate(store)
    promotions = promotion_store(tmp_path)
    promotions.begin(attempt.revision_id, request_key="bytes")
    reservation, meta, entry = materialized(attempt)
    path = {
        "retained": attempt.directory / "geometry/output.step",
        "delivered": reservation.path / "output.step",
        "source": reservation.path / "script.py",
    }[changed]
    path.write_bytes(b"different bytes")
    with pytest.raises(ValueError):
        promotions.commit("bytes", reservation, meta, entry)
    assert json.loads((tmp_path / "agentcad.json").read_text())["current"] == "accepted"
    assert promotions.read("bytes")["result"]["lifecycle"] == "pending"


def test_atomic_commit_failure_can_retry_without_double_acceptance(
    tmp_path, monkeypatch
):
    import agentcad.change_promotion as module

    store = project(tmp_path)
    attempt = candidate(store)
    promotions = promotion_store(tmp_path)
    promotions.begin(attempt.revision_id, request_key="interrupted")
    reservation, meta, entry = materialized(attempt)
    original = module.atomic_write_json

    def fail_manifest(path, payload, **kwargs):
        if path.name == "agentcad.json":
            raise OSError("interrupted before manifest replace")
        return original(path, payload, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(module, "atomic_write_json", fail_manifest)
        with pytest.raises(OSError):
            promotions.commit("interrupted", reservation, meta, entry)
    assert promotions.read("interrupted")["result"]["lifecycle"] == "pending"
    assert json.loads((tmp_path / "agentcad.json").read_text())["current"] == "accepted"
    receipt = promotions.commit("interrupted", reservation, meta, entry)
    assert receipt["outcome"] == "promoted"
    assert len(json.loads((tmp_path / "agentcad.json").read_text())["versions"]) == 2


def test_killed_commit_after_metadata_write_recovers_one_acceptance(tmp_path):
    store = project(tmp_path)
    attempt = candidate(store)
    promotions = promotion_store(tmp_path)
    promotions.begin(attempt.revision_id, request_key="killed")
    reservation, meta, entry = materialized(attempt)
    inputs = tmp_path / "replay-inputs.json"
    inputs.write_text(json.dumps({"meta": meta, "entry": entry}))
    script = """
import json, os, sys
from pathlib import Path
import agentcad.change_promotion as module
from agentcad.versioning import VersionReservation
root = Path(sys.argv[1])
data = json.loads((root / 'replay-inputs.json').read_text())
entry = data['entry']
path = root / entry['path']
reservation = VersionReservation(entry['version'], entry['label'], path.name, path)
original = module.atomic_write_json
def die_before_acceptance(path, payload, **kwargs):
    if path.name == 'agentcad.json':
        os._exit(23)
    return original(path, payload, **kwargs)
module.atomic_write_json = die_before_acceptance
module.PromotionStore(root).commit('killed', reservation, data['meta'], entry)
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], timeout=20)
    assert result.returncode == 23
    assert (reservation.path / "meta.json").exists()
    assert json.loads((tmp_path / "agentcad.json").read_text())["current"] == "accepted"
    assert promotions.read("killed")["result"]["lifecycle"] == "pending"
    receipt = promotions.commit("killed", reservation, meta, entry)
    assert receipt["outcome"] == "promoted"
    assert promotions.commit("killed", reservation, meta, entry) == receipt
    assert len(json.loads((tmp_path / "agentcad.json").read_text())["versions"]) == 2


def test_concurrent_duplicate_commit_returns_one_durable_receipt(tmp_path):
    store = project(tmp_path)
    attempt = candidate(store)
    promotions = promotion_store(tmp_path)
    promotions.begin(attempt.revision_id, request_key="duplicate")
    reservation, meta, entry = materialized(attempt)
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(
            pool.map(
                lambda _: promotions.commit(
                    "duplicate", reservation, copy.deepcopy(meta), entry
                ),
                range(8),
            )
        )
    assert receipts == [receipts[0]] * 8
    assert len(json.loads((tmp_path / "agentcad.json").read_text())["versions"]) == 2


def test_manifest_and_receipt_are_one_authoritative_write(tmp_path, monkeypatch):
    import agentcad.change_promotion as module

    store = project(tmp_path)
    attempt = candidate(store)
    promotions = promotion_store(tmp_path)
    promotions.begin(attempt.revision_id, request_key="atomic")
    original = module.atomic_write_json
    writes = []

    def observe(path, payload, **kwargs):
        if path.name == "agentcad.json":
            writes.append(copy.deepcopy(payload))
        return original(path, payload, **kwargs)

    monkeypatch.setattr(module, "atomic_write_json", observe)
    promotions.commit("atomic", *materialized(attempt))
    assert len(writes) == 1
    assert writes[0]["current"] == "longer"
    assert promotions.read("atomic")["result"]["outcome"] == "promoted"


def test_receipt_is_stable_after_later_acceptance(tmp_path):
    store = project(tmp_path)
    first = candidate(store)
    promotions = promotion_store(tmp_path)
    promotions.begin(first.revision_id, request_key="first")
    receipt = promotions.commit("first", *materialized(first))
    second = candidate(store, label="second")
    promotions.begin(second.revision_id, request_key="second")
    next_receipt = promotions.commit("second", *materialized(second))
    assert next_receipt["promoted_revision_before"] == first.revision_id
    assert promotions.read("first")["result"] == receipt
    assert promotions.accepted("cad")["revision_id"] == second.revision_id


def test_cad_acceptance_preserves_an_independent_target(tmp_path):
    store = project(tmp_path)
    attempt = candidate(store)
    promotions = promotion_store(tmp_path)
    promotions.begin(attempt.revision_id, request_key="cad-only")
    manifest = promotions._manifest()
    state = promotions._state(manifest)
    state["targets"]["robot"] = {"revision_id": "separate-robot-handoff"}
    promotions._write(manifest, state)
    before = promotions.accepted("robot")
    assert (
        promotions.commit("cad-only", *materialized(attempt))["outcome"] == "promoted"
    )
    assert promotions.accepted("robot") == before


def test_corrupt_ledger_is_not_replaced(tmp_path):
    store = project(tmp_path)
    attempt = candidate(store)
    promotions = promotion_store(tmp_path)
    promotions.begin(attempt.revision_id, request_key="corrupt")
    path = tmp_path / "agentcad.json"
    manifest = json.loads(path.read_text())
    manifest["promotion_state"]["operations"] = {}
    atomic_write_json(path, manifest)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        promotions.begin(attempt.revision_id, request_key="new")
    assert path.read_bytes() == before
