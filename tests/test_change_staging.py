"""Stage, inspect and explicitly accept the exact saved CAD revision."""

# collect-on-default-profile

import importlib.util
import json
import asyncio
import shlex
from pathlib import Path

import pytest

from agentcad.cli import cli
from agentcad.change_store import ChangeStore

QUIET = ["--no-preview", "--no-diff", "--no-view", "--no-daemon"]
BRACKET = """plate = Box(60, 30, 6) - Pos(-20, 0, 0) * Cylinder(3, 12) - Pos(20, 0, 0) * Cylinder(3, 12)
show_object(fillet(plate.edges().filter_by(Axis.Z), radius=1), name="bracket")
"""
CQ_BRACKET = """import cadquery as cq
part = cq.Workplane('XY').box(60,30,6).faces('>Z').workplane().pushPoints([(-20,0),(20,0)]).hole(6)
show_object(part.edges('|Z').fillet(1), name='bracket')
"""


def invoke(runner, args, exit_code=0):
    result = runner.invoke(cli, args)
    assert result.exit_code == exit_code, result.output
    return json.loads(result.stdout)


def init(runner, runtime="build123d"):
    return invoke(runner, ["init", "--name", "stage-review", "--runtime", runtime])


def promote(runner, revision, key, *, exit_code=0):
    return invoke(runner, ["promote", revision, "--idempotency-key", key], exit_code)


@pytest.mark.parametrize(
    "runtime,source",
    [
        ("build123d", BRACKET),
        pytest.param(
            "cadquery",
            CQ_BRACKET,
            marks=pytest.mark.skipif(
                importlib.util.find_spec("cadquery") is None,
                reason="optional CadQuery extra",
            ),
        ),
    ],
)
def test_stage_and_promote_saved_bytes_without_executing_mutated_source(
    runner, isolated_dir, runtime, source
):
    init(runner, runtime)
    script = isolated_dir / "part.py"
    script.write_text(source)
    baseline = invoke(runner, ["run", "part.py", "--label", "accepted", *QUIET])
    manifest_before = (isolated_dir / "agentcad.json").read_bytes()
    accepted_bytes = Path(baseline["outputs"]["step"]).read_bytes()
    script.write_text(
        source.replace("60, 30, 6", "80, 30, 6").replace("60,30,6", "80,30,6")
    )
    staged = invoke(runner, ["run", "part.py", "--label", "longer", "--stage", *QUIET])
    revision = staged["change"]["revision_id"]
    assert staged["status"] == "staged"
    assert staged["promotion_mode"] == "staged"
    assert staged["current_advanced"] is False
    assert staged["outputs"]["step"] is None
    assert staged["change"]["validation_checks"]["promotion_ready"] is True
    snapshot_bytes = Path(staged["change"]["geometry"]).read_bytes()
    assert snapshot_bytes != accepted_bytes
    assert (isolated_dir / "agentcad.json").read_bytes() == manifest_before
    assert (
        ChangeStore(isolated_dir).read(revision)["candidate"]["promotion_mode"]
        == "staged"
    )
    context = invoke(runner, ["context"])
    assert context["working_change"]["revision_id"] == revision
    assert context["working_change"]["promotion_mode"] == "staged"
    script.write_text("raise RuntimeError('must never execute during promotion')\n")
    accepted = promote(runner, revision, "accept-longer")
    assert accepted["status"] == "success"
    assert accepted["promotion"]["outcome"] == "promoted"
    assert accepted["promotion"]["promotion_mode"] == "staged"
    assert accepted["current_advanced"] is True
    assert Path(accepted["outputs"]["step"]).read_bytes() == snapshot_bytes
    assert Path(accepted["outputs"]["script"]).read_text() != script.read_text()
    assert Path(baseline["outputs"]["step"]).read_bytes() == accepted_bytes
    manifest = json.loads((isolated_dir / "agentcad.json").read_text())
    assert manifest["current"] == "longer"
    assert len(manifest["versions"]) == 2


def test_staged_import_is_keyed_separately_from_automatic_import(runner, isolated_dir):
    source = Path(__file__).parent / "fixtures/real_world/cadgenbench_101_agentcad.step"
    args = [
        "import",
        str(source),
        "--init",
        "--no-view",
        "--no-diff",
        "--no-daemon",
        "--no-report",
        "--no-inspection-image",
        "--idempotency-key",
        "lab",
    ]
    staged = invoke(runner, [*args, "--stage"])
    assert staged["status"] == "staged"
    before = (isolated_dir / "agentcad.json").read_bytes()
    working = (isolated_dir / ".agentcad/changes/working.json").read_bytes()
    replay = invoke(runner, [*args, "--stage"])
    assert replay["change"]["revision_id"] == staged["change"]["revision_id"]
    assert replay["idempotency"]["replayed"] is True
    assert (isolated_dir / ".agentcad/changes/working.json").read_bytes() == working
    conflict = invoke(runner, args, exit_code=1)
    assert conflict["reason"] == "idempotency_conflict"
    assert (isolated_dir / "agentcad.json").read_bytes() == before
    accepted = promote(runner, staged["change"]["revision_id"], "accept-lab")
    assert accepted["outputs"]["source"].endswith("source.step")
    assert (
        Path(accepted["outputs"]["step"]).read_bytes()
        == Path(staged["change"]["geometry"]).read_bytes()
    )


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize(
    "runtime,geometry",
    [
        (
            "build123d",
            "plate = Box(length, 30, 6) - Pos(-20, 0, 0) * Cylinder(3, 12) - Pos(20, 0, 0) * Cylinder(3, 12)\n"
            "bracket = fillet(plate.edges().filter_by(Axis.Z), radius=1)\n"
            "pin = Pos(0, 50, 0) * Cylinder(2, 12)\n",
        ),
        pytest.param(
            "cadquery",
            "import cadquery as cq\n"
            "plate = cq.Workplane('XY').box(length, 30, 6).faces('>Z').workplane().pushPoints([(-20, 0), (20, 0)]).hole(6)\n"
            "bracket = plate.edges('|Z').fillet(1)\n"
            "pin = cq.Workplane('XY').center(0, 50).circle(2).extrude(12)\n",
            marks=pytest.mark.skipif(
                importlib.util.find_spec("cadquery") is None,
                reason="optional CadQuery extra",
            ),
        ),
    ],
)
def test_staged_promotion_preserves_parts_groups_and_parameter_overrides(
    runner, isolated_dir, runtime, geometry, deferred
):
    init(runner, runtime)
    script = isolated_dir / "assembly.py"
    script.write_text(
        "length = 60\n"
        + geometry
        + 'show_object(bracket, options={"id": "bracket", "name": "Mounting Bracket", "part_of": "mount", "group_color": "steelblue"})\n'
        + 'show_object(pin, options={"id": "pin", "name": "Pivot Pin", "part_of": "mount"})\n'
    )
    baseline = invoke(
        runner,
        ["run", "assembly.py", "--label", "accepted", "--params", "length=60", *QUIET],
    )
    options = []
    if deferred:
        (isolated_dir / "checks.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "checks": [
                        {
                            "check_id": "length",
                            "kind": "dimension",
                            "axis": "x",
                            "expected_mm": 80,
                        }
                    ],
                }
            )
        )
        options = ["--checks", "checks.json", "--defer-checks"]
    staged = invoke(
        runner,
        [
            "run",
            "assembly.py",
            "--label",
            "longer",
            "--params",
            "length=80",
            "--stage",
            *options,
            *QUIET,
        ],
        exit_code=1 if deferred else 0,
    )
    revision = staged["change"]["revision_id"]
    saved_step = Path(staged["change"]["geometry"]).read_bytes()
    saved_source = script.read_bytes()
    script.write_text("raise RuntimeError('promotion must use the saved candidate')\n")
    if deferred:
        invoke(runner, ["check-spec", revision, "--complete"])
    accepted = promote(runner, revision, "accept-assembly")
    parts = invoke(runner, ["parts", "list", "current"])
    assert parts["part_count"] == 2
    assert parts["group_count"] == 1
    assert [p["id"] for p in parts["parts"]] == ["bracket", "pin"]
    assert all(p["id_source"] == "explicit" for p in parts["parts"])
    assert all(p["part_of"] == "mount" for p in parts["parts"])
    assert parts["groups"] == baseline["groups"]
    assert parts["groups"][0]["part_ids"] == ["bracket", "pin"]
    assert parts["groups"][0]["color"] == "steelblue"
    for part_id in ("bracket", "pin"):
        shown = invoke(runner, ["parts", "show", "current", part_id])
        assert shown["part"] == next(p for p in parts["parts"] if p["id"] == part_id)
    meta_path = Path(accepted["outputs"]["step"]).parent / "meta.json"
    meta = json.loads(meta_path.read_text())
    assert meta["params"] == {"length": 80}
    assert meta["parts"] == staged["parts"] == parts["parts"]
    assert meta["groups"] == staged["groups"] == parts["groups"]
    assert staged["params"] == {"length": 80}
    assert Path(accepted["outputs"]["step"]).read_bytes() == saved_step
    assert Path(accepted["outputs"]["script"]).read_bytes() == saved_source
    saved_meta = meta_path.read_bytes()
    replay = promote(runner, revision, "accept-assembly")
    assert replay["promotion"] == accepted["promotion"]
    assert meta_path.read_bytes() == saved_meta


def test_failed_staged_check_requires_new_promotion_key_after_completion(
    runner, isolated_dir
):
    init(runner)
    (isolated_dir / "part.py").write_text(BRACKET)
    checks = {
        "schema_version": 1,
        "checks": [
            {"check_id": "length", "kind": "dimension", "axis": "x", "expected_mm": 60}
        ],
    }
    (isolated_dir / "requirements.json").write_text(json.dumps(checks))
    staged = invoke(
        runner,
        [
            "run",
            "part.py",
            "--label",
            "experiment",
            "--stage",
            "--defer-checks",
            "--checks",
            "requirements.json",
            *QUIET,
        ],
        exit_code=1,
    )
    revision = staged["change"]["revision_id"]
    refused = promote(runner, revision, "too-early", exit_code=1)
    assert refused["promotion"]["outcome"] == "not_promoted"
    assert any(
        row["reason"] == "required_check_pending"
        for row in refused["promotion"]["blockers"]
    )
    completed = invoke(runner, ["check-spec", revision, "--complete"])
    assert completed["current_advanced"] is False
    assert (
        json.loads((isolated_dir / "agentcad.json").read_text()).get("current") is None
    )
    replay = promote(runner, revision, "too-early", exit_code=1)
    assert replay["promotion"] == refused["promotion"]
    accepted = promote(runner, revision, "after-checks")
    assert accepted["promotion"]["outcome"] == "promoted"


def test_promotion_replay_and_status_never_restore_an_older_selection(
    runner, isolated_dir
):
    init(runner)
    (isolated_dir / "part.py").write_text(BRACKET)
    staged = invoke(
        runner, ["run", "part.py", "--label", "experiment", "--stage", *QUIET]
    )
    revision = staged["change"]["revision_id"]
    accepted = promote(runner, revision, "one")
    newer = invoke(runner, ["run", "part.py", "--label", "newer", *QUIET])
    before = (isolated_dir / "agentcad.json").read_bytes()
    working = (isolated_dir / ".agentcad/changes/working.json").read_bytes()
    for args in (
        ["promote", revision, "--idempotency-key", "one"],
        ["promote", "--idempotency-key", "one", "--status"],
    ):
        replay = invoke(runner, args)
        assert replay["promotion"] == accepted["promotion"]
        assert replay["current_advanced"] is False
        assert replay["accepted_revision_now"] == newer["change"]["revision_id"]
        assert (isolated_dir / "agentcad.json").read_bytes() == before
        assert (isolated_dir / ".agentcad/changes/working.json").read_bytes() == working
    conflict = promote(runner, newer["change"]["revision_id"], "one", exit_code=1)
    assert conflict["reason"] == "promotion_request_conflict"


def test_superseded_stage_is_refused_and_dry_run_cannot_stage(runner, isolated_dir):
    init(runner)
    (isolated_dir / "part.py").write_text(BRACKET)
    staged = invoke(runner, ["run", "part.py", "--label", "old", "--stage", *QUIET])
    newer = invoke(runner, ["run", "part.py", "--label", "newer", *QUIET])
    refused = promote(runner, staged["change"]["revision_id"], "old", exit_code=1)
    assert refused["promotion"]["reason_codes"] == ["selection_changed"]
    assert refused["current_advanced"] is False
    assert refused["accepted_revision_now"] == newer["change"]["revision_id"]
    bad = invoke(
        runner,
        ["run", "part.py", "--label", "bad", "--stage", "--dry-run", *QUIET],
        exit_code=1,
    )
    assert bad["reason"] == "checks_need_retention"


def test_repair_action_preserves_explicit_staging(runner, isolated_dir):
    init(runner)
    script = isolated_dir / "part.py"
    script.write_text(BRACKET + "raise RuntimeError('repair me')\n")
    failed = invoke(
        runner,
        ["run", "part.py", "--label", "experiment", "--stage", *QUIET],
        exit_code=1,
    )
    action = failed["next_actions"][0]
    assert "--stage" in action
    script.write_text(BRACKET)
    repaired = invoke(runner, shlex.split(action)[1:] + QUIET)
    assert repaired["status"] == "staged"
    assert (
        json.loads((isolated_dir / "agentcad.json").read_text()).get("current") is None
    )


def test_status_and_terminal_replay_do_not_need_mutable_source(runner, isolated_dir):
    init(runner)
    (isolated_dir / "part.py").write_text(BRACKET)
    stage = invoke(runner, ["run", "part.py", "--label", "save", "--stage", *QUIET])
    revision = stage["change"]["revision_id"]
    accepted = promote(runner, revision, "saved")
    Path(stage["change"]["source"]).write_text("corrupted after the original decision")
    for args in (
        ["promote", "--idempotency-key", "saved", "--status"],
        ["promote", revision, "--idempotency-key", "saved"],
    ):
        replay = invoke(runner, args)
        assert replay["promotion"] == accepted["promotion"]
        assert replay["current_advanced"] is False


def test_pending_promotion_can_be_looked_up_resumed_and_not_accepted_by_legacy_recover(
    runner, isolated_dir, monkeypatch
):
    import agentcad.change_promotion as module

    init(runner)
    (isolated_dir / "part.py").write_text(BRACKET)
    stage = invoke(
        runner, ["run", "part.py", "--label", "experiment", "--stage", *QUIET]
    )
    revision = stage["change"]["revision_id"]
    original = module._write_bytes_once

    def interrupt_output(path, data):
        original(path, data)
        if path.name == "output.step":
            raise OSError("interrupted after materializing bytes")

    with monkeypatch.context() as patch:
        patch.setattr(module, "_write_bytes_once", interrupt_output)
        failed = promote(runner, revision, "resume", exit_code=1)
    assert failed["reason"] == "promotion_unavailable"
    status = invoke(runner, ["promote", "--idempotency-key", "resume", "--status"])
    assert status["status"] == "pending"
    assert status["outputs"]["step"] is None
    operation = module.PromotionStore(isolated_dir).read("resume")
    directory = operation["materialization"]["dir_name"]
    # Even if metadata is lost, the saved reservation still binds these bytes.
    (isolated_dir / directory / "meta.json").unlink()
    refused = invoke(runner, ["recover", directory, "--make-current"], exit_code=1)
    assert refused["reason"] == "promotion_required"
    accepted = promote(runner, revision, "resume")
    assert accepted["version"] == 1
    assert (
        len(json.loads((isolated_dir / "agentcad.json").read_text())["versions"]) == 1
    )


@pytest.mark.parametrize("kind", ["run", "import_cad"])
def test_registered_mcp_stage_and_promote_use_selected_build_root(
    runner, isolated_dir, kind
):
    from agentcad.mcp.server import mcp

    def call(name, args):
        result = asyncio.run(mcp.call_tool(name, args))
        if isinstance(result, tuple):
            return result[1]
        if hasattr(result, "structuredContent"):
            return result.structuredContent
        return json.loads(result[0].text)

    project = isolated_dir / "robot prototype"
    project.mkdir()
    init_payload = invoke(
        runner,
        ["init", "--name", "configured", "--build-dir", str(project / "artifacts")],
    )
    common = {"cwd": str(isolated_dir), "build_dir": str(project / "artifacts")}
    if kind == "run":
        source = isolated_dir / "prototype.py"
        source.write_text(BRACKET)
        args = {
            **common,
            "script": str(source),
            "output": "experiment",
            "stage": True,
            "preview": False,
            "view": False,
            "diff": False,
        }
    else:
        source = isolated_dir / "prototype.step"
        source.write_bytes(
            (
                Path(__file__).parent
                / "fixtures/real_world/cadgenbench_101_agentcad.step"
            ).read_bytes()
        )
        args = {
            **common,
            "file": str(source),
            "label": "experiment",
            "stage": True,
            "report": False,
            "report_images": False,
            "view": False,
            "diff": False,
        }
    staged = call(kind, args)
    assert staged["status"] == "staged", staged
    assert staged["current_advanced"] is False
    assert Path(staged["change"]["geometry"]).is_relative_to(project / "artifacts")
    source.unlink()
    accepted = call(
        "promote",
        {
            **common,
            "revision": staged["change"]["revision_id"],
            "idempotency_key": "mcp-review",
        },
    )
    assert accepted["status"] == "success", accepted
    assert accepted["promotion"]["promotion_mode"] == "staged"
    path = project / "artifacts/agentcad.json"
    before = path.read_bytes()
    status = call(
        "promote", {**common, "idempotency_key": "mcp-review", "status": True}
    )
    assert status["promotion"] == accepted["promotion"]
    assert status["current_advanced"] is False
    assert path.read_bytes() == before
