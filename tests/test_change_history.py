"""Find retained failures and compare exact saved evidence without mutation."""

# collect-on-default-profile

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from agentcad.cli import cli
from agentcad.change_store import ChangeStore

QUIET = ["--no-preview", "--no-diff", "--no-view", "--no-daemon"]


def invoke(runner, args, code=0):
    result = runner.invoke(cli, args)
    assert result.exit_code == code, result.output
    return json.loads(result.stdout)


def snapshot(root):
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.fixture
def history(runner, isolated_dir):
    invoke(runner, ["init", "--name", "retained-history"])
    script = isolated_dir / "part.py"
    script.write_text(
        "plate=Box(60,30,6)-Pos(-20,0,0)*Cylinder(3,12)-Pos(20,0,0)*Cylinder(3,12)\nshow_object(plate)\n"
    )
    base = invoke(runner, ["run", "--experimental-changes", "part.py", "--label", "same-label", *QUIET])
    script.write_text(
        "denominator=int('0')\nvalue=1/denominator\nshow_object(Box(80,30,6))\n"
    )
    failed = invoke(
        runner, ["run", "--experimental-changes", "part.py", "--label", "same-label", "--stage", *QUIET], 1
    )
    script.unlink()
    return base, failed


def test_history_list_show_use_ids_and_preserve_every_saved_byte(
    runner, isolated_dir, history
):
    base, failed = history
    before = snapshot(isolated_dir)
    page = invoke(runner, ["changes", "--experimental-changes", "list"])
    assert page["current_advanced"] is False
    assert [row["revision_id"] for row in page["changes"]] == [
        failed["change"]["revision_id"],
        base["change"]["revision_id"],
    ]
    assert page["changes"][0]["is_working"] is True
    assert page["changes"][0]["is_accepted"] is False
    assert page["changes"][1]["is_accepted"] is True
    assert page["changes"][0]["result_status"] == "failed"
    shown = invoke(runner, ["changes", "--experimental-changes", "show", "working"])
    assert shown["change"]["revision_id"] == failed["change"]["revision_id"]
    assert (
        Path(shown["source"]["path"]).read_bytes()
        == Path(failed["change"]["source"]).read_bytes()
    )
    assert shown["diagnostics"]["message"] or shown["diagnostics"]["error"]
    assert shown["checks"]["validation_state"] == "invalid"
    accepted = invoke(runner, ["changes", "--experimental-changes", "show", "accepted"])
    assert accepted["change"]["revision_id"] == base["change"]["revision_id"]
    assert accepted["promotions"][0]["promotion"]["outcome"] == "promoted"
    assert "current_advanced" not in accepted["promotions"][0]
    assert json.loads(Path(accepted["records"]["promotion_history"]).read_text())[
        "promotion_state"
    ]["operations"]
    assert snapshot(isolated_dir) == before


def test_history_pagination_is_stable_when_newer_attempts_arrive(
    runner, isolated_dir, history
):
    base, failed = history
    first = invoke(runner, ["changes", "--experimental-changes", "list", "--limit", "1"])
    assert first["next_before"] == failed["change"]["revision_id"]
    store = ChangeStore(isolated_dir)
    store.begin(
        command="run",
        label="in-progress",
        source=b"saved source",
        source_name="new.py",
        model={},
    )
    second = invoke(
        runner, ["changes", "--experimental-changes", "list", "--limit", "1", "--before", first["next_before"]]
    )
    assert [row["revision_id"] for row in second["changes"]] == [
        base["change"]["revision_id"]
    ]
    assert second["next_before"] is None
    newest = invoke(runner, ["changes", "--experimental-changes", "show", "working"])
    assert newest["change"]["lifecycle"] == "in_progress"
    assert newest["change"]["result_status"] is None
    assert newest["geometry"] is None
    assert newest["checks"]["validation_state"] == "pending"


def test_damaged_attempt_does_not_hide_other_history_and_cannot_compare(
    runner, isolated_dir, history
):
    base, failed = history
    Path(failed["change"]["source"]).write_bytes(b"damaged retained bytes")
    before = snapshot(isolated_dir)
    page = invoke(runner, ["changes", "--experimental-changes", "list"])
    assert page["changes"][0]["integrity"] == "unavailable"
    assert page["changes"][1]["integrity"] == "verified"
    shown = invoke(runner, ["changes", "--experimental-changes", "show", failed["change"]["revision_id"]], 1)
    assert shown["reason"] == "change_evidence_unavailable"
    compared = invoke(
        runner,
        [
            "changes", "--experimental-changes",
            "compare",
            base["change"]["revision_id"],
            failed["change"]["revision_id"],
        ],
        1,
    )
    assert compared["reason"] == "change_evidence_unavailable"
    assert snapshot(isolated_dir) == before


@pytest.mark.parametrize("ref", ["same-label", "r_" + "f" * 32, "../outside"])
def test_labels_paths_and_unknown_revisions_never_select_or_create(
    runner, isolated_dir, history, ref
):
    before = snapshot(isolated_dir)
    result = invoke(runner, ["changes", "--experimental-changes", "show", ref], 1)
    assert result["current_advanced"] is False
    assert result["reason"] in {
        "invalid_revision_reference",
        "change_evidence_unavailable",
    }
    assert snapshot(isolated_dir) == before


@pytest.mark.parametrize(
    "runtime,source",
    [
        (
            "build123d",
            "length=60\nplate=Box(length,30,6)-Pos(-20,0,0)*Cylinder(3,12)-Pos(20,0,0)*Cylinder(3,12)\nshow_object(fillet(plate.edges().filter_by(Axis.Z),1), options={'id':'bracket'})\nshow_object(Pos(0,50,0)*Cylinder(2,12), options={'id':'pin'})\n",
        ),
        pytest.param(
            "cadquery",
            "import cadquery as cq\nlength=60\nplate=cq.Workplane('XY').box(length,30,6).faces('>Z').workplane().pushPoints([(-20,0),(20,0)]).hole(6)\nshow_object(plate.edges('|Z').fillet(1), options={'id':'bracket'})\nshow_object(cq.Workplane('XY').center(0,50).circle(2).extrude(12), options={'id':'pin'})\n",
            marks=pytest.mark.skipif(
                importlib.util.find_spec("cadquery") is None,
                reason="optional CadQuery extra",
            ),
        ),
    ],
)
def test_compare_saved_measurements_checks_and_policy_without_claiming_geometry_equality(
    runner, isolated_dir, runtime, source
):
    invoke(runner, ["init", "--name", "bracket", "--runtime", runtime])
    script = isolated_dir / "assembly.py"
    script.write_text(source)
    baseline = invoke(runner, ["run", "--experimental-changes", "assembly.py", "--label", "accepted", *QUIET])
    base_id = baseline["change"]["revision_id"]
    checks = {
        "schema_version": 1,
        "checks": [
            {"check_id": "length", "kind": "dimension", "axis": "x", "expected_mm": 80},
            {
                "check_id": "mounts",
                "kind": "preserve_cylinders",
                "diameter_mm": 6,
                "axis": "z",
                "baseline_revision_id": base_id,
                "tolerance_mm": 0.05,
            },
        ],
    }
    (isolated_dir / "checks.json").write_text(json.dumps(checks))
    moved = (
        source.replace("length=60", "length=80")
        .replace("Pos(20,0,0)", "Pos(23,0,0)")
        .replace("(20,0)]", "(23,0)]")
    )
    script.write_text(moved)
    candidate = invoke(
        runner,
        [
            "run", "--experimental-changes",
            "assembly.py",
            "--label",
            "failed-edit",
            "--checks",
            "checks.json",
            "--stage",
            *QUIET,
        ],
        1,
    )
    script.write_text("raise RuntimeError('never execute history source')\n")
    before = snapshot(isolated_dir)
    comparison = invoke(runner, ["changes", "--experimental-changes", "compare", "accepted", "working"])
    assert comparison["baseline"]["revision_id"] == base_id
    assert comparison["candidate"]["revision_id"] == candidate["change"]["revision_id"]
    assert comparison["metrics"]["dimensions"]["x"]["delta"] == 20
    assert comparison["geometry_comparison"]["status"] == "not_run"
    assert comparison["source"]["same_bytes"] is False
    assert comparison["policy"]["changed"] is True
    assert set(comparison["policy"]["added_checks"]) == {"length", "mounts"}
    mounts = next(row for row in comparison["checks"] if row["check_id"] == "mounts")
    assert mounts["baseline"] is None
    assert mounts["candidate"]["state"] == "fail"
    assert mounts["candidate"]["observed"]["max_displacement_mm"] == pytest.approx(3)
    assert comparison["next_actions"][0].startswith("agentcad diff ")
    assert snapshot(isolated_dir) == before


def test_compare_no_output_failure_retains_evidence_without_using_accepted_geometry(
    runner, isolated_dir, history
):
    base, failed = history
    result = invoke(runner, ["changes", "--experimental-changes", "compare", "accepted", "working"])
    assert result["geometry_comparison"]["status"] == "unavailable"
    assert result["geometry_comparison"]["candidate"] is None
    assert result["metrics"]["dimensions"]["x"]["delta"] is None
    assert result["candidate"]["is_working"] is True
    assert result["candidate"]["is_accepted"] is False


def test_compare_aliases_remain_bound_if_another_caller_selects_newer_work(
    runner, isolated_dir, history, monkeypatch
):
    from agentcad.change_history import ChangeHistory

    base, failed = history
    original = ChangeHistory._show
    selected = []

    def select_after_baseline(self, revision, selection, state):
        result = original(self, revision, selection, state)
        if not selected:
            selected.append(
                self.store.begin(
                    command="run",
                    label="newer",
                    source=b"new source",
                    source_name="new.py",
                    model={},
                ).revision_id
            )
        return result

    monkeypatch.setattr(ChangeHistory, "_show", select_after_baseline)
    compared = invoke(runner, ["changes", "--experimental-changes", "compare", "accepted", "working"])
    assert compared["baseline"]["revision_id"] == base["change"]["revision_id"]
    assert compared["candidate"]["revision_id"] == failed["change"]["revision_id"]
    assert ChangeStore(isolated_dir).working_revision_id == selected[0]
    assert compared["current_advanced"] is False


def test_explicit_root_after_subcommand_bypasses_a_damaged_default_history(
    runner, isolated_dir
):
    root = isolated_dir / "good history"
    invoke(runner, ["init", "--name", "selected", "--build-dir", str(root)])
    broken = isolated_dir / "broken"
    broken.mkdir()
    (broken / "agentcad.json").write_text("not valid JSON")
    (isolated_dir / "agentcad.toml").write_text('build_dir = "./broken"\n')
    before = snapshot(isolated_dir)
    for args in (
        ["changes", "--experimental-changes", "list", "--build-dir", str(root)],
        ["changes", "--experimental-changes", "--build-dir", str(root), "list"],
    ):
        result = invoke(runner, args)
        assert result["changes"] == []
        assert Path(result["build_root"]) == root.resolve()
    assert snapshot(isolated_dir) == before


def test_policy_redefinition_is_not_reported_as_a_geometry_repair(runner, isolated_dir):
    invoke(runner, ["init", "--name", "requirements-change"])
    script = isolated_dir / "bracket.py"
    script.write_text(
        "show_object(Box(60,30,6)-Pos(-20,0,0)*Cylinder(3,12)-Pos(20,0,0)*Cylinder(3,12))\n"
    )
    checks = {
        "schema_version": 1,
        "checks": [
            {"check_id": "length", "kind": "dimension", "axis": "x", "expected_mm": 80}
        ],
    }
    path = isolated_dir / "checks.json"
    path.write_text(json.dumps(checks))
    old = invoke(
        runner,
        [
            "run", "--experimental-changes",
            "bracket.py",
            "--label",
            "failed",
            "--checks",
            str(path),
            "--stage",
            *QUIET,
        ],
        1,
    )
    checks.update(actor="human", rationale="The drawing calls for 60 mm")
    checks["checks"][0]["expected_mm"] = 60
    path.write_text(json.dumps(checks))
    new = invoke(
        runner,
        [
            "run", "--experimental-changes",
            "bracket.py",
            "--label",
            "redefined",
            "--checks",
            str(path),
            "--stage",
            *QUIET,
        ],
    )
    before = snapshot(isolated_dir)
    result = invoke(
        runner,
        [
            "changes", "--experimental-changes",
            "compare",
            old["change"]["revision_id"],
            new["change"]["revision_id"],
        ],
    )
    assert result["source"]["same_bytes"] is True
    assert result["metrics"]["dimensions"]["x"]["delta"] == 0
    assert result["policy"]["changed_checks"] == ["length"]
    assert result["policy"]["change"]["actor"] == "human"
    length = next(row for row in result["checks"] if row["check_id"] == "length")
    assert length["baseline"]["state"] == "fail"
    assert length["candidate"]["state"] == "pass"
    assert length["definition_changed"] is True
    assert result["geometry_comparison"]["status"] == "not_run"
    assert snapshot(isolated_dir) == before


def test_same_source_with_different_params_remains_a_model_change(runner, isolated_dir):
    invoke(runner, ["init", "--name", "params"])
    (isolated_dir / "part.py").write_text(
        "length=60\nshow_object(Box(length,30,6)-Cylinder(3,12))\n"
    )
    old = invoke(
        runner, ["run", "--experimental-changes", "part.py", "--label", "short", "--params", "length=60", *QUIET]
    )
    new = invoke(
        runner,
        [
            "run", "--experimental-changes",
            "part.py",
            "--label",
            "long",
            "--params",
            "length=80",
            "--stage",
            *QUIET,
        ],
    )
    result = invoke(
        runner,
        [
            "changes", "--experimental-changes",
            "compare",
            old["change"]["revision_id"],
            new["change"]["revision_id"],
        ],
    )
    assert result["source"]["same_bytes"] is True
    assert result["model"]["changed"] is True
    assert result["params"] == {
        "baseline": {"length": 60},
        "candidate": {"length": 80},
        "changed": True,
    }
    assert result["metrics"]["dimensions"]["x"]["delta"] == 20


def test_empty_legacy_and_uninitialized_histories_are_read_only(runner, isolated_dir):
    missing = invoke(runner, ["changes", "--experimental-changes", "list"], 1)
    assert missing["status"] == "error"
    assert snapshot(isolated_dir) == {}
    invoke(runner, ["init", "--name", "legacy"])
    before = snapshot(isolated_dir)
    assert invoke(runner, ["changes", "--experimental-changes", "list"])["changes"] == []
    assert (
        invoke(runner, ["changes", "--experimental-changes", "show", "accepted"], 1)["reason"]
        == "accepted_change_unavailable"
    )
    assert (
        invoke(runner, ["changes", "--experimental-changes", "show", "working"], 1)["reason"]
        == "working_change_unavailable"
    )
    assert snapshot(isolated_dir) == before
    path = isolated_dir / "agentcad.json"
    legacy = json.loads(path.read_text())
    legacy.update(
        current="old",
        versions=[
            {"version": 1, "label": "old", "status": "success", "path": "v1_old/"}
        ],
    )
    path.write_text(json.dumps(legacy))
    before = snapshot(isolated_dir)
    listed = invoke(runner, ["changes", "--experimental-changes", "list"])
    assert listed["selection"]["accepted_revision_id"] is None
    assert listed["selection"]["accepted_version"]["label"] == "old"
    assert (
        invoke(runner, ["changes", "--experimental-changes", "show", "accepted"], 1)["reason"]
        == "accepted_change_unavailable"
    )
    assert snapshot(isolated_dir) == before


def test_show_includes_specific_static_failure_without_an_extra_record_read(
    runner, isolated_dir
):
    invoke(runner, ["init", "--name", "static-failure"])
    (isolated_dir / "missing.py").write_text("value = 3\n")
    failed = invoke(runner, ["run", "--experimental-changes", "missing.py", "--label", "no-capture", *QUIET], 1)
    assert failed["status"] == "validation_error"
    before = snapshot(isolated_dir)
    shown = invoke(runner, ["changes", "--experimental-changes", "show", failed["change"]["revision_id"]])
    assert shown["diagnostics"]["static_checks"][0]["check"] == "show_object_missing"
    assert "show_object" in shown["diagnostics"]["static_checks"][0]["message"]
    assert shown["diagnostics"]["static_check_count"] == 1
    assert shown["geometry"] is None
    assert snapshot(isolated_dir) == before


def test_bounded_diagnostics_and_artifacts_keep_full_immutable_records(
    runner, isolated_dir
):
    invoke(runner, ["init", "--name", "long-diagnostics"])
    store = ChangeStore(isolated_dir)
    attempt = store.begin(
        command="run",
        label="failed",
        source=b"saved source",
        source_name="broken.py",
        model={},
    )
    outputs = {}
    for i in range(51):
        path = isolated_dir / f"log-{i}.txt"
        path.write_text(f"diagnostic {i}")
        outputs[f"report_{i}"] = str(path)
    attempt.finish(
        {
            "command": "run",
            "status": "failed",
            "message": "m" * 3000,
            "error": "e" * 4000,
            "script_output": "o" * 5000,
            "outputs": outputs,
        }
    )
    before = snapshot(isolated_dir)
    shown = invoke(runner, ["changes", "--experimental-changes", "show", attempt.revision_id])
    assert shown["diagnostics"]["truncated"] is True
    assert all(
        len(shown["diagnostics"][key]) == 2000
        for key in ("message", "error", "script_output")
    )
    assert shown["artifact_count"] == 51
    assert len(shown["artifacts"]) == 50
    assert shown["artifacts_omitted"] == 1
    record = json.loads(Path(shown["records"]["outcome"]).read_text())
    assert len(record["response"]["script_output"]) == 5000
    assert len(record["artifacts"]) == 51
    assert all(Path(item["path"]).is_file() for item in shown["artifacts"])
    assert snapshot(isolated_dir) == before


@pytest.mark.parametrize("damage", ["corrupt", "missing"])
@pytest.mark.parametrize("damaged_index", [0, 1], ids=["older", "latest"])
def test_bad_cursor_and_damaged_request_preserve_other_rows(
    runner, isolated_dir, history, damage, damaged_index
):
    damaged = history[damaged_index]
    healthy = history[1 - damaged_index]
    request = Path(damaged["change"]["request"])
    if damage == "missing":
        request.unlink()
    else:
        request.write_text("not valid JSON")
    (request.parent.parent / ("r_" + "0" * 32)).write_text("not a directory")
    (request.parent.parent / "unrelated").mkdir()
    before = snapshot(isolated_dir)
    page = invoke(runner, ["changes", "--experimental-changes", "list"])
    assert page["total"] == 2
    assert page["count"] == 2
    assert page["changes"][0]["revision_id"] == healthy["change"]["revision_id"]
    assert page["changes"][0]["integrity"] == "verified"
    assert page["changes"][1]["revision_id"] == damaged["change"]["revision_id"]
    assert page["changes"][1]["integrity"] == "unavailable"
    assert page["changes"][1]["message"]
    assert page["changes"][1]["is_working"] is (damaged_index == 1)
    assert page["changes"][1]["is_accepted"] is (damaged_index == 0)
    first = invoke(runner, ["changes", "--experimental-changes", "list", "--limit", "1"])
    last = invoke(
        runner, ["changes", "--experimental-changes", "list", "--limit", "1", "--before", first["next_before"]]
    )
    assert last["total"] == 2
    assert last["changes"] == [page["changes"][1]]
    assert last["next_before"] is None
    assert (
        invoke(runner, ["changes", "--experimental-changes", "show", healthy["change"]["revision_id"]])["change"][
            "integrity"
        ]
        == "verified"
    )
    assert (
        invoke(runner, ["changes", "--experimental-changes", "show", damaged["change"]["revision_id"]], 1)[
            "reason"
        ]
        == "change_evidence_unavailable"
    )
    bad = invoke(runner, ["changes", "--experimental-changes", "list", "--before", "r_" + "f" * 32], 1)
    assert bad["reason"] == "invalid_history_cursor"
    assert snapshot(isolated_dir) == before


def test_pending_checks_and_automatic_intent_remain_pending_when_read(
    runner, isolated_dir
):
    invoke(runner, ["init", "--name", "pending"])
    (isolated_dir / "part.py").write_text("show_object(Box(60,30,6)-Cylinder(3,12))\n")
    (isolated_dir / "checks.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "checks": [
                    {
                        "check_id": "length",
                        "kind": "dimension",
                        "axis": "x",
                        "expected_mm": 60,
                    }
                ],
            }
        )
    )
    result = invoke(
        runner,
        [
            "run", "--experimental-changes",
            "part.py",
            "--label",
            "pending",
            "--checks",
            "checks.json",
            "--defer-checks",
            *QUIET,
        ],
        1,
    )
    before = snapshot(isolated_dir)
    shown = invoke(runner, ["changes", "--experimental-changes", "show", "working"])
    assert shown["checks"]["validation_state"] == "pending"
    assert shown["promotions"][0]["promotion"]["lifecycle"] == "pending"
    compared = invoke(
        runner, ["changes", "--experimental-changes", "compare", result["change"]["revision_id"], "working"]
    )
    assert compared["candidate"]["validation_state"] == "pending"
    assert snapshot(isolated_dir) == before


def test_registered_mcp_history_with_configured_root_rejects_unknown_options(
    runner, isolated_dir
):
    from agentcad.mcp.server import mcp

    root = isolated_dir / "artifacts"
    invoke(runner, ["init", "--name", "configured", "--build-dir", str(root)])
    (isolated_dir / "part.py").write_text("show_object(Box(60,30,6)-Cylinder(3,12))\n")
    built = invoke(
        runner,
        ["run", "--experimental-changes", "part.py", "--label", "accepted", "--build-dir", str(root), *QUIET],
    )

    def call(name, args):
        result = asyncio.run(mcp.call_tool(name, {**args, "experimental_changes": True}))
        if isinstance(result, tuple):
            return result[1]
        if hasattr(result, "structuredContent"):
            return result.structuredContent
        return json.loads(result[0].text)

    common = {"cwd": str(isolated_dir), "build_dir": str(root)}
    before = snapshot(isolated_dir)
    listed = call("changes_list", common)
    assert listed["changes"][0]["revision_id"] == built["change"]["revision_id"]
    shown = call("changes_show", {**common, "revision": "accepted"})
    assert Path(shown["source"]["path"]).is_relative_to(root)
    same = call(
        "changes_compare", {**common, "baseline": "accepted", "revision": "working"}
    )
    assert same["source"]["same_bytes"] is True
    with pytest.raises(ValueError, match="Invalid changes_show arguments"):
        call("changes_show", {**common, "revision": "accepted", "restore": True})
    assert snapshot(isolated_dir) == before
