"""Saved CAD is recovered as staged successors without losing current intent."""

# collect-on-default-profile

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from agentcad.cli import cli

FLAG = "--experimental-changes"
QUIET = ["--no-preview", "--no-diff", "--no-view", "--no-daemon"]
SOURCE = """length=60
plate=Box(length,30,6)-Pos(-20,0,0)*Cylinder(3,12)-Pos(20,0,0)*Cylinder(3,12)
show_object(fillet(plate.edges().filter_by(Axis.Z),1), id='bracket', options={'color':'coral','part_of':'mount'})
show_object(Pos(0,50,0)*Cylinder(2,12), id='pin', options={'part_of':'mount'})
"""


def invoke(runner, args, code=0):
    result = runner.invoke(cli, args)
    assert result.exit_code == code, (result.output, result.exception)
    return json.loads(result.stdout)


def files(root):
    return {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


def setup(runner, root):
    invoke(runner, ["init", "--name", "recovery"])
    (root / "part.py").write_text(SOURCE)
    return invoke(runner, ["run", "part.py", "--label", "accepted", FLAG, *QUIET])


def test_restore_creates_a_staged_successor_with_current_requirements(
    runner, isolated_dir
):
    old = setup(runner, isolated_dir)
    checks = {
        "schema_version": 1,
        "checks": [
            {"check_id": "length", "kind": "dimension", "axis": "x", "expected_mm": 80}
        ],
    }
    (isolated_dir / "checks.json").write_text(json.dumps(checks))
    (isolated_dir / "part.py").write_text(SOURCE.replace("length=60", "length=80"))
    latest = invoke(
        runner,
        [
            "run",
            "part.py",
            "--label",
            "longer",
            "--checks",
            "checks.json",
            FLAG,
            *QUIET,
        ],
    )
    before_manifest = (isolated_dir / "agentcad.json").read_bytes()
    old_files = files(isolated_dir / ".agentcad/changes/attempts")
    restored = invoke(
        runner,
        [
            "changes",
            "restore",
            old["change"]["revision_id"],
            "--label",
            "try-old",
            "--idempotency-key",
            "restore:1",
            FLAG,
        ],
    )
    assert restored["command"] == "changes" and restored["action"] == "restore"
    assert restored["current_advanced"] is False
    revision = restored["change"]["revision_id"]
    assert revision not in {
        old["change"]["revision_id"],
        latest["change"]["revision_id"],
    }
    shown = invoke(runner, ["changes", "show", revision, FLAG])
    assert shown["change"]["promotion_mode"] == "staged"
    assert shown["change"]["parent_revision_id"] == latest["change"]["revision_id"]
    assert shown["checks"]["validation_state"] == "invalid"
    assert (
        next(r for r in shown["checks"]["results"] if r["check_id"] == "length")[
            "settings"
        ]["expected_mm"]
        == 80
    )
    assert (
        Path(shown["geometry"]["path"]).read_bytes()
        == Path(old["change"]["geometry"]).read_bytes()
    )
    assert Path(shown["source"]["path"]).read_text() == SOURCE
    assert {p["id"] for p in shown["parts"]} == {"bracket", "pin"}
    assert shown["recovery"]["source_revision_id"] == old["change"]["revision_id"]
    assert (isolated_dir / "agentcad.json").read_bytes() == before_manifest
    assert all(
        (isolated_dir / ".agentcad/changes/attempts" / p).read_bytes() == value
        for p, value in old_files.items()
    )


def test_repair_executes_edited_source_as_staged_work_and_replays(runner, isolated_dir):
    base = setup(runner, isolated_dir)
    (isolated_dir / "repair.py").write_text(SOURCE.replace("length=60", "length=80"))
    args = [
        "changes",
        "repair",
        base["change"]["revision_id"],
        "repair.py",
        "--label",
        "repaired",
        "--idempotency-key",
        "repair:1",
        FLAG,
    ]
    repaired = invoke(runner, args)
    assert repaired["status"] == "staged"
    assert repaired["current_advanced"] is False
    assert repaired["change"]["revision_id"] != base["change"]["revision_id"]
    assert (
        json.loads((isolated_dir / "agentcad.json").read_text())["current"]
        == "accepted"
    )
    before = files(isolated_dir)
    replay = invoke(runner, args)
    assert replay["replayed"] is True
    assert replay["change"]["revision_id"] == repaired["change"]["revision_id"]
    assert files(isolated_dir) == before
    (isolated_dir / "repair.py").unlink()
    before = files(isolated_dir)
    status = invoke(
        runner, ["changes", "repair", "--idempotency-key", "repair:1", "--status", FLAG]
    )
    assert status["change"]["revision_id"] == repaired["change"]["revision_id"]
    assert files(isolated_dir) == before


def test_no_output_restore_keeps_failed_source_and_never_substitutes_accepted_geometry(
    runner, isolated_dir
):
    setup(runner, isolated_dir)
    (isolated_dir / "bad.py").write_text(
        "value=1/int('0')\nshow_object(Box(80,30,6))\n"
    )
    failed = invoke(runner, ["run", "bad.py", "--label", "bad", FLAG, *QUIET], 1)
    restored = invoke(
        runner,
        [
            "changes",
            "restore",
            failed["change"]["revision_id"],
            "--label",
            "recover-source",
            "--idempotency-key",
            "source:1",
            FLAG,
        ],
    )
    shown = invoke(runner, ["changes", "show", restored["change"]["revision_id"], FLAG])
    assert shown["geometry"] is None
    assert shown["checks"]["promotion_ready"] is False
    assert (
        Path(shown["source"]["path"]).read_text()
        == (isolated_dir / "bad.py").read_text()
    )
    assert (
        json.loads((isolated_dir / "agentcad.json").read_text())["current"]
        == "accepted"
    )


def recovery_args(action, revision="working", source=None, key="recover:1"):
    return [
        "changes",
        action,
        revision,
        *([source] if source else []),
        "--label",
        "recovered",
        "--idempotency-key",
        key,
        FLAG,
    ]


@pytest.mark.parametrize("action", ["restore", "repair"])
def test_flag_denial_precedes_any_side_effect(runner, isolated_dir, action):
    setup(runner, isolated_dir)
    before = files(isolated_dir)
    result = invoke(
        runner,
        recovery_args(action, source="part.py" if action == "repair" else None)[:-1],
        1,
    )
    assert result["reason"] == "experimental_changes_required"
    assert files(isolated_dir) == before


def test_restore_replay_keeps_original_alias_and_recovers_a_lost_response(
    runner, isolated_dir
):
    original = setup(runner, isolated_dir)
    args = recovery_args("restore", "accepted")
    restored = invoke(runner, args)
    # Acceptance and selection can move later without changing a known key.
    invoke(runner, ["run", "part.py", "--label", "later", FLAG, *QUIET])
    before = files(isolated_dir)
    replay = invoke(runner, args)
    assert replay["change"]["revision_id"] == restored["change"]["revision_id"]
    assert replay["recovery"]["source_revision_id"] == original["change"]["revision_id"]
    assert files(isolated_dir) == before
    response = Path(restored["idempotency"]["request"]).with_name("response.json")
    response.unlink()
    before = files(isolated_dir)
    status = invoke(
        runner,
        ["changes", "restore", "--idempotency-key", "recover:1", "--status", FLAG],
    )
    assert status["status"] == "success"
    assert status["change"]["revision_id"] == restored["change"]["revision_id"]
    assert files(isolated_dir) == before


def test_changed_key_inputs_and_status_options_are_rejected(runner, isolated_dir):
    setup(runner, isolated_dir)
    invoke(runner, recovery_args("restore"))
    before = files(isolated_dir)
    conflict = invoke(runner, recovery_args("restore", "accepted"), 1)
    assert conflict["reason"] == "idempotency_conflict"
    bad_status = invoke(
        runner,
        [
            "changes",
            "restore",
            "accepted",
            "--idempotency-key",
            "recover:1",
            "--status",
            FLAG,
        ],
        1,
    )
    assert bad_status["reason"] == "recovery_status_options"
    assert files(isolated_dir) == before


@pytest.mark.parametrize("artifact", ["source", "geometry"])
def test_damaged_source_revision_never_changes_selection(
    runner, isolated_dir, artifact
):
    original = setup(runner, isolated_dir)
    Path(original["change"][artifact]).write_bytes(b"damaged")
    selection = (isolated_dir / ".agentcad/changes/working.json").read_bytes()
    manifest = (isolated_dir / "agentcad.json").read_bytes()
    result = invoke(runner, recovery_args("restore"), 1)
    assert result["reason"] == "change_evidence_unavailable"
    assert (isolated_dir / ".agentcad/changes/working.json").read_bytes() == selection
    assert (isolated_dir / "agentcad.json").read_bytes() == manifest


def test_restore_retains_artifact_bytes_and_parameter_metadata(runner, isolated_dir):
    setup(runner, isolated_dir)
    from agentcad.change_store import Attempt, ChangeStore

    store = ChangeStore(isolated_dir)
    # A real multipart run supplies its source/STEP/part/group metadata. Add a
    # small saved inspection catalogue to verify opaque artifacts survive too.
    original = store.begin(
        command="run",
        label="with-evidence",
        source=SOURCE.encode(),
        source_name="part.py",
        model={
            "runtime": "build123d",
            "params": '{"length":60}',
            "validation_profile": "deliverable",
        },
        promotion_mode="staged",
    )
    accepted = invoke(runner, ["changes", "show", "accepted", FLAG])
    response = json.loads(Path(accepted["records"]["core"]).read_text())["response"]
    catalogue = isolated_dir / "catalogue.json"
    catalogue.write_text(
        '{"review":"bracket and pin", "original_revision":"historical"}'
    )
    response["outputs"] = {"report": "catalogue.json"}
    original.prepare(response, geometry=Path(accepted["geometry"]["path"]))
    original.finish(response)
    restored = invoke(runner, recovery_args("restore", original.revision_id))
    shown = invoke(runner, ["changes", "show", restored["change"]["revision_id"], FLAG])
    assert shown["model"]["params"] == '{"length":60}'
    assert shown["groups"] == accepted["groups"]
    assert shown["params"] == accepted["params"]
    assert shown["artifact_count"] == 1
    assert Path(shown["artifacts"][0]["path"]).read_bytes() == catalogue.read_bytes()
    assert shown["artifacts"][0]["path"] != str(catalogue)
    assert {p["id"] for p in shown["parts"]} == {"bracket", "pin"}


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
def test_failed_bore_repair_preserves_requirements_and_can_be_explicitly_accepted(
    runner, isolated_dir, runtime
):
    source = (
        SOURCE
        if runtime == "build123d"
        else """import cadquery as cq
length=60
plate=cq.Workplane('XY').box(length,30,6).faces('>Z').workplane().pushPoints([(-20,0),(20,0)]).hole(6)
show_object(plate.edges('|Z').fillet(1), options={'id':'bracket','part_of':'mount'})
show_object(cq.Workplane('XY').center(0,50).circle(2).extrude(12), options={'id':'pin','part_of':'mount'})
"""
    )
    invoke(runner, ["init", "--name", "repair", "--runtime", runtime])
    (isolated_dir / "part.py").write_text(source)
    base = invoke(runner, ["run", "part.py", "--label", "baseline", FLAG, *QUIET])
    checks = {
        "schema_version": 1,
        "checks": [
            {
                "check_id": "bores",
                "kind": "preserve_cylinders",
                "axis": "z",
                "diameter_mm": 6,
                "baseline_revision_id": base["change"]["revision_id"],
            }
        ],
    }
    (isolated_dir / "checks.json").write_text(json.dumps(checks))
    wrong = source.replace("Cylinder(3,12)", "Cylinder(4,12)").replace(
        ".hole(6)", ".hole(8)"
    )
    (isolated_dir / "bad.py").write_text(wrong)
    failed = invoke(
        runner,
        [
            "run",
            "bad.py",
            "--label",
            "bad-bore",
            "--checks",
            "checks.json",
            "--stage",
            FLAG,
            *QUIET,
        ],
        1,
    )
    accepted_before = (isolated_dir / "agentcad.json").read_bytes()
    repaired = invoke(
        runner, recovery_args("repair", failed["change"]["revision_id"], "part.py")
    )
    assert repaired["status"] == "staged"
    assert repaired["change"]["validation_checks"]["promotion_ready"] is True
    assert (isolated_dir / "agentcad.json").read_bytes() == accepted_before
    shown = invoke(runner, ["changes", "show", repaired["change"]["revision_id"], FLAG])
    assert shown["model"]["runtime"] == runtime
    assert any(
        r["check_id"] == "bores" and r["state"] == "pass"
        for r in shown["checks"]["results"]
    )
    promoted = invoke(
        runner,
        [
            "promote",
            repaired["change"]["revision_id"],
            "--idempotency-key",
            "accept-repair",
            FLAG,
        ],
    )
    assert promoted["current_advanced"] is True


def test_registered_mcp_recovery_uses_configured_root_and_boolean_gate(
    runner, isolated_dir
):
    from agentcad.mcp.server import mcp

    invoke(runner, ["init", "--name", "configured", "--build-dir", "cad outputs"])
    (isolated_dir / "part.py").write_text(SOURCE)
    invoke(
        runner,
        [
            "run",
            "part.py",
            "--label",
            "baseline",
            "--build-dir",
            "cad outputs",
            FLAG,
            *QUIET,
        ],
    )

    async def call(name, args):
        result = await mcp.call_tool(name, args)
        return json.loads(result[0].text)

    args = {
        "cwd": str(isolated_dir),
        "build_dir": "cad outputs",
        "idempotency_key": "mcp-recovery",
        "revision": "accepted",
        "label": "restored",
        "experimental_changes": True,
    }
    result = asyncio.run(call("changes_restore", args))
    assert result["action"] == "restore"
    assert result["change"]["geometry"].startswith(str(isolated_dir / "cad outputs"))
    before = files(isolated_dir)
    with pytest.raises(ValueError):
        asyncio.run(call("changes_restore", {**args, "experimental_changes": "true"}))
    with pytest.raises(ValueError):
        asyncio.run(
            call("changes_repair", {**args, "source": "part.py", "stage": False})
        )
    status = asyncio.run(
        call(
            "changes_restore",
            {
                "cwd": str(isolated_dir),
                "build_dir": "cad outputs",
                "idempotency_key": "mcp-recovery",
                "status": True,
                "experimental_changes": True,
            },
        )
    )
    assert status["change"]["revision_id"] == result["change"]["revision_id"]
    assert files(isolated_dir) == before


@pytest.mark.parametrize("action", ["restore", "repair"])
def test_stopped_after_selection_before_binding_never_executes_again(
    runner, isolated_dir, monkeypatch, action
):
    from agentcad.change_recovery import RecoveryRequest

    setup(runner, isolated_dir)
    args = recovery_args(action, source="part.py" if action == "repair" else None)

    def stop(*args):
        raise KeyboardInterrupt()

    with monkeypatch.context() as patch:
        patch.setattr(RecoveryRequest, "bind_attempt", stop)
        result = runner.invoke(cli, args)
        assert result.exit_code != 0
    working = json.loads((isolated_dir / ".agentcad/changes/working.json").read_text())[
        "revision_id"
    ]
    before = files(isolated_dir)
    status = invoke(
        runner,
        ["changes", action, "--idempotency-key", "recover:1", "--status", FLAG],
        1,
    )
    assert status["reason"] == "recovery_interrupted"
    assert status["change"]["revision_id"] == working
    assert status["change"]["geometry"] is None
    with monkeypatch.context() as patch:
        patch.setattr(
            "agentcad.commands.run._run_impl", lambda *a, **kw: pytest.fail("reran CAD")
        )
        replay = invoke(runner, args, 1)
    assert replay["change"]["revision_id"] == working
    assert files(isolated_dir) == before


@pytest.mark.parametrize("action", ["restore", "repair"])
def test_lost_reply_after_outcome_is_readable_without_writes(
    runner, isolated_dir, monkeypatch, action
):
    from agentcad.change_recovery import RecoveryRequest

    setup(runner, isolated_dir)
    args = recovery_args(action, source="part.py" if action == "repair" else None)

    def stop(*args):
        raise KeyboardInterrupt()

    with monkeypatch.context() as patch:
        patch.setattr(RecoveryRequest, "complete", stop)
        assert runner.invoke(cli, args).exit_code != 0
    before = files(isolated_dir)
    status = invoke(
        runner, ["changes", action, "--idempotency-key", "recover:1", "--status", FLAG]
    )
    assert status["status"] == ("success" if action == "restore" else "staged")
    assert status["change"]["validation_checks"]["promotion_ready"] is True
    assert files(isolated_dir) == before


def test_selection_race_refuses_instead_of_changing_pinned_requirements(
    runner, isolated_dir, monkeypatch
):
    from agentcad.change_recovery import RecoveryRequest
    from agentcad.change_store import ChangeStore

    setup(runner, isolated_dir)
    original_plan = RecoveryRequest.plan
    raced = []

    def interleave(self, *args):
        plan = original_plan(self, *args)
        newer = ChangeStore(isolated_dir).begin(
            command="run",
            label="newer-work",
            source=SOURCE.encode(),
            source_name="part.py",
            model={
                "runtime": "build123d",
                "params": None,
                "validation_profile": "deliverable",
            },
            promotion_mode="staged",
        )
        raced.append(newer.revision_id)
        return plan

    with monkeypatch.context() as patch:
        patch.setattr(RecoveryRequest, "plan", interleave)
        result = invoke(runner, recovery_args("restore"), 1)
    assert result["reason"] == "recovery_selection_changed"
    assert (
        json.loads((isolated_dir / ".agentcad/changes/working.json").read_text())[
            "revision_id"
        ]
        == raced[0]
    )
    before = files(isolated_dir)
    replay = invoke(runner, recovery_args("restore"), 1)
    assert replay["reason"] == "recovery_selection_changed"
    assert files(isolated_dir) == before


def test_repair_inherits_parameter_overrides_and_can_explicitly_clear_them(
    runner, isolated_dir
):
    setup(runner, isolated_dir)
    pinned = invoke(
        runner,
        [
            "run",
            "part.py",
            "--label",
            "pinned",
            "--params",
            "length=70",
            "--stage",
            FLAG,
            *QUIET,
        ],
    )
    (isolated_dir / "edited.py").write_text(SOURCE.replace("length=60", "length=80"))
    repaired = invoke(
        runner, recovery_args("repair", pinned["change"]["revision_id"], "edited.py")
    )
    shown = invoke(runner, ["changes", "show", repaired["change"]["revision_id"], FLAG])
    assert shown["metrics"]["dimensions"]["x"] == pytest.approx(70)
    cleared = invoke(
        runner,
        [
            *recovery_args(
                "repair",
                pinned["change"]["revision_id"],
                "edited.py",
                key="clear-overrides",
            ),
            "--params",
            "",
        ],
    )
    shown = invoke(runner, ["changes", "show", cleared["change"]["revision_id"], FLAG])
    assert shown["metrics"]["dimensions"]["x"] == pytest.approx(80)


def test_imported_cad_restores_exact_source_and_geometry_but_rejects_script_repair(
    runner, isolated_dir
):
    original = setup(runner, isolated_dir)
    imported = invoke(
        runner,
        [
            "import",
            original["change"]["geometry"],
            "--label",
            "lab",
            "--stage",
            "--no-daemon",
            FLAG,
        ],
    )
    restored = invoke(
        runner, recovery_args("restore", imported["change"]["revision_id"])
    )
    assert (
        Path(restored["change"]["source"]).read_bytes()
        == Path(
            invoke(
                runner, ["changes", "show", imported["change"]["revision_id"], FLAG]
            )["source"]["path"]
        ).read_bytes()
    )
    assert (
        Path(restored["change"]["geometry"]).read_bytes()
        == Path(imported["change"]["geometry"]).read_bytes()
    )
    before = (isolated_dir / ".agentcad/changes/working.json").read_bytes()
    refused = invoke(
        runner,
        recovery_args(
            "repair", imported["change"]["revision_id"], "part.py", key="not-python"
        ),
        1,
    )
    assert refused["reason"] == "repair_requires_script"
    assert (isolated_dir / ".agentcad/changes/working.json").read_bytes() == before


@pytest.mark.parametrize("status", [False, True])
def test_busy_request_still_rejects_conflicting_operation_or_inputs(
    runner, isolated_dir, monkeypatch, status
):
    from agentcad.import_requests import _locked

    setup(runner, isolated_dir)
    restored = invoke(runner, recovery_args("restore"))
    directory = Path(restored["idempotency"]["request"]).parent
    before = files(isolated_dir)
    monkeypatch.setattr("agentcad.import_requests._LOCK_WAIT_SECONDS", 0)
    args = (
        ["changes", "repair", "--idempotency-key", "recover:1", "--status", FLAG]
        if status
        else [a if a != "recovered" else "different" for a in recovery_args("restore")]
    )
    with _locked(directory / "execution.lock") as acquired:
        assert acquired
        conflict = invoke(runner, args, 1)
    assert conflict["reason"] == "idempotency_conflict"
    assert files(isolated_dir) == before
