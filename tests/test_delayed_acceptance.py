"""Delayed checks settle the original normal-run intent, at most once."""

# collect-on-default-profile

import asyncio
import importlib.util
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agentcad.cli import cli
from agentcad.change_promotion import PromotionStore
from agentcad.change_store import ChangeStore
from agentcad.versioning import _version_lock, atomic_write_json

QUIET = ["--no-preview", "--no-diff", "--no-view", "--no-daemon"]
REQUIREMENTS = {
    "schema_version": 1,
    "checks": [
        {"check_id": "length", "kind": "dimension", "axis": "x", "expected_mm": 80}
    ],
}


def invoke(runner, args, code=0):
    result = runner.invoke(cli, args)
    assert result.exit_code == code, result.output
    return json.loads(result.stdout)


@pytest.fixture
def deferred(tmp_path):
    """Real STEP plus a legacy accepted predecessor, without executing CAD."""
    from build123d import Box, Cylinder, Pos, Compound, export_step

    atomic_write_json(
        tmp_path / "agentcad.json",
        {
            "name": "prototype",
            "current": "baseline",
            "versions": [
                {
                    "version": 1,
                    "label": "baseline",
                    "status": "success",
                    "path": "v1_baseline/",
                }
            ],
        },
    )
    geometry = Compound(
        children=[
            Box(80, 30, 6)
            - Pos(-20, 0, 0) * Cylinder(3, 12)
            - Pos(20, 0, 0) * Cylinder(3, 12),
            Pos(0, 50, 0) * Cylinder(2, 12),
        ]
    )
    source = tmp_path / "written.step"
    export_step(geometry, source)
    store = ChangeStore(tmp_path)
    attempt = store.begin(
        command="run",
        label="longer",
        source=b"saved bracket and pin source",
        source_name="prototype.py",
        model={"runtime": "build123d"},
        requirements=REQUIREMENTS,
        defer_checks=True,
    )
    attempt.prepare(
        {"command": "run", "status": "success", "validation": {"is_valid": True}},
        geometry=source,
    )
    attempt.finish({"command": "run", "status": "checks_pending"})
    return store, attempt


def manifest(root):
    return json.loads((root / "agentcad.json").read_text())


def test_pending_intent_is_not_a_refusal_and_readers_never_finish_it(
    deferred, runner, monkeypatch
):
    store, attempt = deferred
    monkeypatch.chdir(store.root)
    key = "auto:" + attempt.revision_id
    original = (store.root / "agentcad.json").read_bytes()
    for args in (
        ["check-spec", "--experimental-changes", attempt.revision_id],
        ["promote", "--experimental-changes", "--idempotency-key", key, "--status"],
    ):
        payload = invoke(runner, args)
        assert payload["promotion"]["lifecycle"] == "pending"
        assert payload["promotion"]["outcome"] is None
        assert payload["promotion"]["jobs"][0]["revision_id"] == attempt.revision_id
        assert payload["current_advanced"] is False
    resumed = invoke(runner, ["promote", "--experimental-changes", attempt.revision_id, "--idempotency-key", key])
    assert resumed["status"] == "pending"
    assert (store.root / "agentcad.json").read_bytes() == original


def test_completion_after_restart_accepts_exact_saved_bytes_once(
    deferred, runner, monkeypatch
):
    store, attempt = deferred
    monkeypatch.chdir(store.root)
    original = {p: p.read_bytes() for p in attempt.directory.glob("*.json")}
    working = (store.root / ".agentcad/changes/working.json").read_bytes()
    completed = invoke(runner, ["check-spec", "--experimental-changes", attempt.revision_id, "--complete"])
    assert completed["passed"] is True
    assert completed["current_advanced"] is True
    assert completed["promotion"]["outcome"] == "promoted"
    assert completed["accepted_version_before"]["label"] == "baseline"
    assert (
        Path(completed["outputs"]["step"]).read_bytes()
        == attempt.directory.joinpath("geometry/output.step").read_bytes()
    )
    assert (
        Path(completed["outputs"]["script"]).read_bytes()
        == attempt.source_path.read_bytes()
    )
    assert "view" in completed["next_actions"][0]
    assert manifest(store.root)["current"] == "longer"
    before = (store.root / "agentcad.json").read_bytes()
    replay = invoke(runner, ["check-spec", "--experimental-changes", attempt.revision_id, "--complete"])
    assert replay["promotion"] == completed["promotion"]
    assert replay["current_advanced"] is False
    assert (store.root / "agentcad.json").read_bytes() == before
    assert (store.root / ".agentcad/changes/working.json").read_bytes() == working
    assert all(p.read_bytes() == data for p, data in original.items())
    assert len(manifest(store.root)["versions"]) == 2


def test_selecting_newer_work_cancels_before_checks_and_away_back_never_revives(
    deferred,
    runner,
    monkeypatch,
):
    store, attempt = deferred
    newer = store.begin(
        command="run",
        label="newer",
        source=b"new source",
        source_name="new.py",
        model={},
    )
    key = "auto:" + attempt.revision_id
    cancelled = PromotionStore(store.root).read(key)["result"]
    assert cancelled["outcome"] == "not_promoted"
    assert cancelled["reason_codes"] == ["selection_changed"]
    assert cancelled["validation_state"] == "pending"
    # The future restore adapter increments the selection generation too.
    with _version_lock(store.root):
        atomic_write_json(
            store.root / ".agentcad/changes/working.json",
            {
                "schema_version": 1,
                "revision_id": attempt.revision_id,
                "selection_generation": store.read(newer.revision_id)["request"][
                    "selection_generation"
                ]
                + 1,
            },
        )
    completed = ChangeStore(store.root).complete_checks(attempt.revision_id)
    assert completed["receipt"]["validation_state"] == "valid"
    assert completed["promotion"] == cancelled
    assert completed["current_advanced"] is False
    assert manifest(store.root)["current"] == "baseline"
    monkeypatch.chdir(store.root)
    read = invoke(runner, ["check-spec", "--experimental-changes", attempt.revision_id])
    assert read["passed"] is True
    assert read["promotion"]["validation_state"] == "pending"
    assert any("Repeating --complete cannot reopen" in note for note in read["notes"])
    assert read["next_actions"][0].startswith("agentcad context")


def test_policy_successor_cancels_old_intent_even_without_new_geometry(deferred):
    store, attempt = deferred
    changed = {
        "schema_version": 1,
        "actor": "human",
        "rationale": "New length target",
        "checks": [
            {"check_id": "length", "kind": "dimension", "axis": "x", "expected_mm": 90}
        ],
    }
    newer = store.begin(
        command="run",
        label="new-policy",
        source=b"syntax error",
        source_name="new.py",
        model={},
        requirements=changed,
    )
    assert store.read(newer.revision_id)["request"]["check_policy"]["version"] == 2
    assert (
        store.complete_checks(attempt.revision_id)["promotion"]["outcome"]
        == "not_promoted"
    )
    assert manifest(store.root)["current"] == "baseline"


def test_changed_accepted_predecessor_refuses_without_substituting_it(deferred):
    store, attempt = deferred
    value = manifest(store.root)
    value["current"] = None
    atomic_write_json(store.root / "agentcad.json", value)
    completed = store.complete_checks(attempt.revision_id)
    assert completed["receipt"]["validation_state"] == "valid"
    assert completed["promotion"]["reason_codes"] == ["accepted_target_changed"]
    assert completed["current_advanced"] is False
    assert manifest(store.root)["current"] is None


@pytest.mark.parametrize("state", ["fail", "unavailable"])
def test_failed_or_unavailable_check_settles_forever(deferred, monkeypatch, state):
    from agentcad import change_contract as contract
    from agentcad.change_store import Attempt

    store, attempt = deferred

    def evaluate(self, candidate, policy, response):
        return [contract.make_check_result(candidate, policy, "length", state)]

    monkeypatch.setattr(Attempt, "_evaluate", evaluate)
    first = store.complete_checks(attempt.revision_id)
    assert first["promotion"]["outcome"] == "not_promoted"
    assert first["current_advanced"] is False
    monkeypatch.setattr(
        Attempt, "_evaluate", lambda *a: pytest.fail("recomputed terminal evidence")
    )
    replay = ChangeStore(store.root).complete_checks(attempt.revision_id)
    assert replay["promotion"] == first["promotion"]
    assert manifest(store.root)["current"] == "baseline"


def test_concurrent_completions_publish_one_acceptance(deferred):
    store, attempt = deferred
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: ChangeStore(store.root).complete_checks(attempt.revision_id),
                range(4),
            )
        )
    assert sum(result["current_advanced"] for result in results) == 1
    assert all(result["promotion"] == results[0]["promotion"] for result in results)
    assert len(manifest(store.root)["versions"]) == 2


def test_selection_during_slow_checks_cancels_before_result_arrives(
    deferred, monkeypatch
):
    from agentcad.change_store import Attempt

    store, attempt = deferred
    entered, release = threading.Event(), threading.Event()
    original = Attempt._evaluate

    def slow(self, *args):
        entered.set()
        assert release.wait(10)
        return original(self, *args)

    monkeypatch.setattr(Attempt, "_evaluate", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(store.complete_checks, attempt.revision_id)
        assert entered.wait(10)
        try:
            store.begin(
                command="run",
                label="newer",
                source=b"new",
                source_name="new.py",
                model={},
            )
            assert (
                PromotionStore(store.root).read("auto:" + attempt.revision_id)[
                    "result"
                ]["outcome"]
                == "not_promoted"
            )
        finally:
            release.set()
        result = future.result(timeout=10)
    assert result["receipt"]["validation_state"] == "valid"
    assert result["current_advanced"] is False
    assert manifest(store.root)["current"] == "baseline"


@pytest.mark.parametrize("point", ["copy", "committed"])
def test_actual_process_death_recovers_same_automatic_operation(deferred, point):
    store, attempt = deferred
    script = """
import os, sys
from agentcad.change_store import ChangeStore
import agentcad.change_promotion as module
root, revision, point = sys.argv[1:]
if point == "copy":
    original = module._write_bytes_once
    def stop(path, data):
        original(path, data)
        if path.name == "output.step": os._exit(91)
    module._write_bytes_once = stop
else:
    original = module.PromotionStore._write
    def stop(self, manifest, state):
        original(self, manifest, state)
        if any(op["result"].get("outcome") == "promoted" for op in state["operations"].values()): os._exit(91)
    module.PromotionStore._write = stop
ChangeStore(root).complete_checks(revision)
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(store.root), attempt.revision_id, point],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == 91, child.stderr
    operation = PromotionStore(store.root).read("auto:" + attempt.revision_id)
    reserved = operation["materialization"]["dir_name"]
    result = ChangeStore(store.root).complete_checks(attempt.revision_id)
    assert result["promotion"]["outcome"] == "promoted"
    assert result["current_advanced"] is (point == "copy")
    assert result["accepted_version_after"]["path"] == reserved + "/"
    assert len(manifest(store.root)["versions"]) == 2
    assert len(list(store.root.glob("v*_longer"))) == 1


@pytest.mark.parametrize("change_predecessor", [False, True])
def test_interruption_before_enrollment_uses_original_predecessor(
    deferred, monkeypatch, change_predecessor
):
    store, _ = deferred
    # Simulate a retained core written before the enrollment manifest write.
    with monkeypatch.context() as patch:
        patch.setattr(PromotionStore, "ensure_automatic", lambda *a: None)
        attempt = store.begin(
            command="run",
            label="not-enrolled",
            source=b"saved",
            source_name="part.py",
            model={},
            requirements=None,
            defer_checks=True,
        )
        attempt.prepare(
            {"status": "success", "validation": {"is_valid": True}},
            geometry=store.root / "written.step",
        )
    assert PromotionStore(store.root).find("auto:" + attempt.revision_id) is None
    if change_predecessor:
        changed = manifest(store.root)
        changed["current"] = None
        atomic_write_json(store.root / "agentcad.json", changed)
    result = store.complete_checks(attempt.revision_id)
    assert result["promotion"]["outcome"] == (
        "not_promoted" if change_predecessor else "promoted"
    )
    if change_predecessor:
        assert "accepted_target_changed" in result["promotion"]["reason_codes"]
    assert manifest(store.root)["current"] == (
        None if change_predecessor else "not-enrolled"
    )


def test_corrupted_geometry_cannot_accept_and_restored_bytes_can_resume(deferred):
    from agentcad.change_store import ChangeStoreError

    store, attempt = deferred
    geometry = attempt.directory / "geometry/output.step"
    original = geometry.read_bytes()
    geometry.write_bytes(b"corrupted")
    with pytest.raises(ChangeStoreError):
        store.complete_checks(attempt.revision_id)
    assert manifest(store.root)["current"] == "baseline"
    assert (
        PromotionStore(store.root).read("auto:" + attempt.revision_id)["result"][
            "lifecycle"
        ]
        == "pending"
    )
    geometry.write_bytes(original)
    assert (
        store.complete_checks(attempt.revision_id)["promotion"]["outcome"] == "promoted"
    )


@pytest.mark.parametrize(
    "runtime",
    [
        "build123d",
        pytest.param(
            "cadquery",
            marks=pytest.mark.skipif(
                importlib.util.find_spec("cadquery") is None,
                reason="optional CadQuery extra",
            ),
        ),
    ],
)
@pytest.mark.parametrize("damage", ["corrupt", "missing"])
def test_damaged_pending_geometry_does_not_block_healthy_successors(
    runner, isolated_dir, runtime, damage
):
    invoke(runner, ["init", "--name", "supersede-damaged", "--runtime", runtime])
    source = (
        "plate=Box(80,30,6)-Pos(-20,0,0)*Cylinder(3,12)-Pos(20,0,0)*Cylinder(3,12)\n"
        "show_object(fillet(plate.edges().filter_by(Axis.Z),1), id='bracket')\n"
        "show_object(Pos(0,50,0)*Cylinder(2,12), id='pin')\n"
        if runtime == "build123d"
        else "import cadquery as cq\n"
        "plate=cq.Workplane('XY').box(80,30,6).faces('>Z').workplane().pushPoints([(-20,0),(20,0)]).hole(6)\n"
        "show_object(plate.edges('|Z').fillet(1), id='bracket')\n"
        "show_object(cq.Workplane('XY').center(0,50).circle(2).extrude(12), id='pin')\n"
    )
    (isolated_dir / "part.py").write_text(source)
    (isolated_dir / "checks.json").write_text(json.dumps(REQUIREMENTS))
    pending = invoke(
        runner,
        [
            "run",
            "part.py",
            "--label",
            "pending",
            "--checks",
            "checks.json",
            "--defer-checks",
            "--experimental-changes",
            *QUIET,
        ],
        1,
    )
    revision = pending["change"]["revision_id"]
    geometry = Path(pending["change"]["geometry"])
    original = geometry.read_bytes()
    if damage == "corrupt":
        geometry.write_bytes(b"damaged old STEP")
    else:
        geometry.unlink()
    for label in ("healthy", "healthy-again"):
        healthy = invoke(
            runner,
            ["run", "part.py", "--label", label, "--experimental-changes", *QUIET],
        )
        assert healthy["current_advanced"] is True
        assert healthy["change"]["validation_checks"]["validation_state"] == "valid"
    cancelled = PromotionStore(isolated_dir).read("auto:" + revision)["result"]
    assert cancelled["outcome"] == "not_promoted"
    assert cancelled["reason_codes"] == ["selection_changed"]
    assert cancelled["verified_artifacts"] == []
    # Restoring evidence and finishing old checks cannot revive cancelled intent.
    geometry.write_bytes(original)
    before = (isolated_dir / "agentcad.json").read_bytes()
    working = (isolated_dir / ".agentcad/changes/working.json").read_bytes()
    completed = invoke(
        runner, ["check-spec", revision, "--complete", "--experimental-changes"]
    )
    assert completed["passed"] is True
    assert completed["current_advanced"] is False
    assert completed["promotion"] == cancelled
    assert (isolated_dir / "agentcad.json").read_bytes() == before
    assert (isolated_dir / ".agentcad/changes/working.json").read_bytes() == working
    assert manifest(isolated_dir)["current"] == "healthy-again"


@pytest.mark.parametrize("existing_selection", [False, True])
def test_failed_cancellation_does_not_leave_new_work_selected(
    deferred, monkeypatch, existing_selection
):
    store, old = deferred
    working = store.root / ".agentcad/changes/working.json"
    if not existing_selection:
        working.unlink()
    before = working.read_bytes() if working.exists() else None

    def fail(*args):
        raise OSError("cannot save supersession")

    monkeypatch.setattr(PromotionStore, "cancel_superseded", fail)
    with pytest.raises(OSError, match="cannot save supersession"):
        store.begin(
            command="run",
            label="failed-start",
            source=b"new source",
            source_name="new.py",
            model={},
        )
    assert (working.read_bytes() if working.exists() else None) == before
    assert store.working_revision_id == (
        old.revision_id if existing_selection else None
    )
    # Failed startup remains inspectable without replacing the previous selection.
    requests = list(store.root.glob(".agentcad/changes/attempts/*/request.json"))
    assert len(requests) == 2


def test_completion_before_original_run_response_does_not_create_a_second_version(
    runner, isolated_dir, monkeypatch
):
    invoke(runner, ["init", "--name", "fast-checks"])
    (isolated_dir / "part.py").write_text(
        "plate = Box(80,30,6) - Pos(-20,0,0)*Cylinder(3,12) - Pos(20,0,0)*Cylinder(3,12)\n"
        "show_object(fillet(plate.edges().filter_by(Axis.Z),1), name='bracket')\n"
    )
    (isolated_dir / "checks.json").write_text(json.dumps(REQUIREMENTS))
    original = PromotionStore.ensure_automatic
    completions = []

    def finish_before_response(self, revision):
        original(self, revision)
        if not completions:
            completions.append(None)
            completions[0] = self.changes.complete_checks(revision)

    monkeypatch.setattr(PromotionStore, "ensure_automatic", finish_before_response)
    pending = invoke(
        runner,
        [
            "run", "--experimental-changes",
            "part.py",
            "--label",
            "longer",
            "--checks",
            "checks.json",
            "--defer-checks",
            "--idempotency-key",
            "one",
            *QUIET,
        ],
        1,
    )
    assert completions[0]["current_advanced"] is True
    assert pending["status"] == "checks_pending"
    assert pending["current_advanced"] is False
    assert pending["change"]["validation_checks"]["validation_state"] == "pending"
    assert pending["promotion"]["outcome"] is None
    assert pending["accepted_version_after"] is None
    assert len(manifest(isolated_dir)["versions"]) == 1
    assert len(list(isolated_dir.glob("v*_longer"))) == 1
    current = invoke(runner, ["check-spec", "--experimental-changes", pending["change"]["revision_id"]])
    assert current["promotion"]["outcome"] == "promoted"
    assert current["validation_state"] == "valid"


@pytest.mark.parametrize(
    "runtime,geometry",
    [
        (
            "build123d",
            "plate = Box(length,30,6) - Pos(-20,0,0)*Cylinder(3,12) - Pos(20,0,0)*Cylinder(3,12)\nbracket = fillet(plate.edges().filter_by(Axis.Z),1)\npin = Pos(0,50,0)*Cylinder(2,12)\n",
        ),
        pytest.param(
            "cadquery",
            "import cadquery as cq\nbracket = cq.Workplane('XY').box(length,30,6).faces('>Z').workplane().pushPoints([(-20,0),(20,0)]).hole(6).edges('|Z').fillet(1)\npin = cq.Workplane('XY').center(0,50).circle(2).extrude(12)\n",
            marks=pytest.mark.skipif(
                importlib.util.find_spec("cadquery") is None,
                reason="optional CadQuery extra",
            ),
        ),
    ],
)
@pytest.mark.parametrize("staged", [False, True])
def test_cli_pending_to_acceptance_preserves_parts_and_original_run_response(
    runner, isolated_dir, runtime, geometry, staged
):
    invoke(runner, ["init", "--name", "prototype", "--runtime", runtime])
    source = isolated_dir / "assembly.py"
    source.write_text(
        "length=80\n"
        + geometry
        + 'show_object(bracket, options={"id":"bracket","part_of":"mount"})\n'
        'show_object(pin, options={"id":"pin","part_of":"mount"})\n'
    )
    (isolated_dir / "checks.json").write_text(json.dumps(REQUIREMENTS))
    pending = invoke(
        runner,
        [
            "run", "--experimental-changes",
            "assembly.py",
            "--label",
            "longer",
            "--params",
            "length=80",
            "--checks",
            "checks.json",
            "--defer-checks",
            "--idempotency-key",
            "edit-1",
            *(["--stage"] if staged else []),
            *QUIET,
        ],
        1,
    )
    revision = pending["change"]["revision_id"]
    if not staged:
        assert pending["promotion"]["lifecycle"] == "pending"
    else:
        assert "promotion" not in pending
    saved_geometry = Path(pending["change"]["geometry"]).read_bytes()
    source.unlink()
    (isolated_dir / "checks.json").write_text(
        "{}"
    )  # Completion uses the pinned policy.
    completed = invoke(runner, ["check-spec", "--experimental-changes", revision, "--complete"])
    assert completed["passed"] is True
    assert completed["current_advanced"] is (not staged)
    assert manifest(isolated_dir).get("current") == (None if staged else "longer")
    if not staged:
        assert Path(completed["outputs"]["step"]).read_bytes() == saved_geometry
        meta = json.loads(
            Path(completed["outputs"]["step"]).with_name("meta.json").read_text()
        )
        assert meta["parts"] == pending["parts"]
        assert meta["groups"] == pending["groups"]
        assert meta["params"] == {"length": 80}
        assert invoke(runner, ["parts", "list", "current"])["part_count"] == 2
    original = invoke(
        runner, ["run", "--experimental-changes", "--idempotency-key", "edit-1", "--status", "--no-daemon"], 1
    )
    assert original["change"] == pending["change"]
    assert original.get("promotion") == pending.get("promotion")
    assert original["current_advanced"] is False


def test_registered_mcp_import_completion_respects_configured_build_root(
    runner, isolated_dir
):
    from agentcad.mcp.server import mcp

    def call(name, args):
        result = asyncio.run(mcp.call_tool(name, {**args, "experimental_changes": True}))
        if isinstance(result, tuple):
            return result[1]
        if hasattr(result, "structuredContent"):
            return result.structuredContent
        return json.loads(result[0].text)

    root = isolated_dir / "artifacts"
    invoke(runner, ["init", "--name", "intake", "--build-dir", str(root)])
    (isolated_dir / "checks.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "checks": [
                    {
                        "check_id": "solids",
                        "kind": "external",
                        "validator": "not-available",
                        "required": False,
                    },
                    {
                        "check_id": "width",
                        "kind": "dimension",
                        "axis": "x",
                        "expected_mm": 220,
                        "tolerance_mm": 0.01,
                    },
                ],
            }
        )
    )
    common = {"cwd": str(isolated_dir), "build_dir": str(root)}
    pending = call(
        "import_cad",
        {
            **common,
            "file": str(
                Path(__file__).parent
                / "fixtures/real_world/cadgenbench_101_agentcad.step"
            ),
            "label": "lab",
            "checks_file": str(isolated_dir / "checks.json"),
            "defer_checks": True,
            "report": False,
            "report_images": False,
            "view": False,
            "diff": False,
        },
    )
    assert pending["status"] == "checks_pending", pending
    revision = pending["change"]["revision_id"]
    result = call("check_spec", {**common, "file": revision, "complete": True})
    assert result["current_advanced"] is True, result
    assert result["promotion"]["outcome"] == "promoted"
    assert Path(result["outputs"]["step"]).is_relative_to(root)
    assert manifest(root)["current"] == "lab"
    assert not (isolated_dir / "agentcad.json").exists()
    again = call("check_spec", {**common, "file": revision, "complete": True})
    assert again["promotion"] == result["promotion"]
    assert again["current_advanced"] is False
