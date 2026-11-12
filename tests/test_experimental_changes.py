"""Experimental change workflows require an explicit opt-in on every call."""

# collect-on-default-profile

import asyncio
import json
from pathlib import Path

import pytest

from agentcad.cli import cli
from agentcad.change_store import ChangeStore

FLAG = "--experimental-changes"
QUIET = ["--no-preview", "--no-diff", "--no-view", "--no-daemon"]


def invoke(runner, args, code=0):
    result = runner.invoke(cli, args)
    assert result.exit_code == code, (result.output, result.exception)
    return json.loads(result.stdout)


def snapshot(root):
    return {
        str(p.relative_to(root)): p.read_bytes() if p.is_file() else None
        for p in root.rglob("*")
    }


@pytest.mark.parametrize(
    "args",
    [
        ["run", "missing.py", "--label", "edit", "--stage"],
        ["run", "missing.py", "--label", "edit", "--checks", "missing.json"],
        ["run", "missing.py", "--label", "edit", "--defer-checks"],
        ["run", "--idempotency-key", "saved", "--status"],
        ["import", "missing.step", "--init", "--stage"],
        ["changes", "list"],
        ["changes", "show", "working"],
        ["changes", "compare", "accepted", "working"],
        ["promote", "r_" + "a" * 32, "--idempotency-key", "accept"],
        ["check-spec", "r_" + "a" * 32, "--complete"],
        ["view", "r_" + "a" * 32],
    ],
)
def test_missing_flag_refuses_before_reading_inputs_or_writing(
    runner, isolated_dir, args
):
    before = snapshot(isolated_dir)
    response = invoke(runner, args, 1)
    assert response["reason"] == "experimental_changes_required"
    assert FLAG in response["suggestion"]
    assert snapshot(isolated_dir) == before


def test_ordinary_run_keeps_standard_outputs_without_change_state(runner, isolated_dir):
    invoke(runner, ["init", "--name", "ordinary"])
    (isolated_dir / "part.py").write_text("show_object(Box(60,30,6)-Cylinder(3,12))\n")
    built = invoke(runner, ["run", "part.py", "--label", "first", *QUIET])
    assert built["status"] == "success"
    assert Path(built["outputs"]["step"]).is_file()
    assert "change" not in built and "promotion" not in built
    assert "experimental_changes" not in built
    assert not (isolated_dir / ".agentcad/changes").exists()
    assert "promotion_state" not in json.loads(
        (isolated_dir / "agentcad.json").read_text()
    )


def test_opt_in_stages_and_cannot_be_bypassed_on_next_call(runner, isolated_dir):
    invoke(runner, ["init", "--name", "experiment"])
    script = isolated_dir / "part.py"
    script.write_text("show_object(Box(60,30,6)-Cylinder(3,12))\n")
    staged = invoke(
        runner, ["run", "part.py", "--label", "try", FLAG, "--stage", *QUIET]
    )
    assert staged["status"] == "staged"
    assert staged["experimental_changes"]["status"] == "work_in_progress"
    assert FLAG in staged["suggestion"]
    revision = staged["change"]["revision_id"]
    script.write_text("raise RuntimeError('must not execute')\n")
    before = snapshot(isolated_dir)
    denied = invoke(runner, ["run", "part.py", "--label", "bypass", *QUIET], 1)
    assert denied["reason"] == "experimental_changes_required"
    assert snapshot(isolated_dir) == before
    assert "working_change" not in invoke(runner, ["context"])
    assert (
        invoke(runner, ["context", FLAG])["working_change"]["revision_id"] == revision
    )
    before = snapshot(isolated_dir)
    shown = invoke(runner, ["changes", "show", revision, FLAG])
    assert shown["change"]["revision_id"] == revision
    invoke(runner, ["check-spec", revision, FLAG])
    assert snapshot(isolated_dir) == before
    accepted = invoke(
        runner, ["promote", revision, "--idempotency-key", "accept", FLAG]
    )
    assert accepted["current_advanced"] is True
    before = snapshot(isolated_dir)
    invoke(runner, ["promote", "--idempotency-key", "accept", "--status", FLAG])
    assert snapshot(isolated_dir) == before
    script.write_text("show_object(Box(80,30,6))\n")
    for label in ("ordinary-after-acceptance", "ordinary-again"):
        assert "change" not in invoke(
            runner, ["run", "part.py", "--label", label, *QUIET]
        )


def test_existing_declared_policy_requires_opt_in_even_after_acceptance(
    runner, isolated_dir
):
    invoke(runner, ["init", "--name", "existing"])
    (isolated_dir / "part.py").write_text("show_object(Box(60,30,6))\n")
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
    accepted = invoke(
        runner,
        ["run", "part.py", "--label", "old", "--checks", "checks.json", FLAG, *QUIET],
    )
    assert accepted["current_advanced"] is True
    before = snapshot(isolated_dir)
    denied = invoke(runner, ["import", "missing.step", "--init"], 1)
    assert denied["reason"] == "experimental_changes_required"
    assert snapshot(isolated_dir) == before


@pytest.mark.parametrize("dispatch", [False, True])
def test_ordinary_import_key_replays_without_experimental_state(
    runner, isolated_dir, dispatch
):
    invoke(runner, ["init", "--name", "ordinary-import"])
    (isolated_dir / "part.py").write_text("show_object(Box(60,30,6)-Cylinder(3,12))\n")
    built = invoke(runner, ["run", "part.py", "--label", "source", *QUIET])
    args = [
        "import",
        built["outputs"]["step"],
        "--label",
        "adopted",
        "--idempotency-key",
        "ordinary",
        "--no-view",
        "--no-diff",
        "--no-report",
        "--no-inspection-image",
        "--no-daemon",
        "--full-response",
    ]
    if dispatch:
        args = [
            "run",
            built["outputs"]["step"],
            "--label",
            "adopted",
            "--idempotency-key",
            "ordinary",
            *QUIET,
        ]
    first = invoke(runner, args)
    replay = invoke(runner, args)
    assert replay["idempotency"]["replayed"] is True
    assert replay["version"] == first["version"]
    assert "change" not in first and "change" not in replay
    assert not (isolated_dir / ".agentcad/changes").exists()


def test_registered_mcp_requires_explicit_true(runner, isolated_dir):
    from agentcad.mcp.server import mcp

    invoke(runner, ["init", "--name", "mcp"])
    before = snapshot(isolated_dir)

    def call(arguments):
        result = asyncio.run(mcp.call_tool("changes_list", arguments))
        if isinstance(result, tuple):
            return result[1]
        return (
            result.structuredContent
            if hasattr(result, "structuredContent")
            else json.loads(result[0].text)
        )

    assert call({"cwd": str(isolated_dir)})["reason"] == "experimental_changes_required"
    assert (
        call({"cwd": str(isolated_dir), "experimental_changes": False})["reason"]
        == "experimental_changes_required"
    )
    enabled = call({"cwd": str(isolated_dir), "experimental_changes": True})
    assert enabled["changes"] == []
    assert enabled["experimental_changes"]["status"] == "work_in_progress"
    assert call({"cwd": str(isolated_dir)})["reason"] == "experimental_changes_required"
    with pytest.raises(ValueError, match="must be a boolean"):
        call({"cwd": str(isolated_dir), "experimental_changes": "true"})
    assert snapshot(isolated_dir) == before


@pytest.mark.parametrize("missing", ["request.json", "working.json"])
def test_damaged_selected_history_cannot_be_bypassed(runner, isolated_dir, missing):
    invoke(runner, ["init", "--name", "damaged"])
    attempt = ChangeStore(isolated_dir).begin(
        command="run",
        label="saved",
        source=b"saved source",
        source_name="old.py",
        model={},
        promotion_mode="staged",
    )
    path = (
        attempt.directory / missing
        if missing == "request.json"
        else isolated_dir / ".agentcad/changes/working.json"
    )
    path.unlink()
    before = snapshot(isolated_dir)
    response = invoke(runner, ["run", "missing.py", "--label", "bypass", *QUIET], 1)
    assert response["reason"] == "experimental_changes_required"
    assert snapshot(isolated_dir) == before


def test_late_ordinary_build_cannot_accept_over_a_new_experiment(
    runner, isolated_dir, monkeypatch
):
    import agentcad.versioning as versioning

    invoke(runner, ["init", "--name", "overlap"])
    (isolated_dir / "part.py").write_text("show_object(Box(60,30,6))\n")
    base = invoke(runner, ["run", "part.py", "--label", "accepted", *QUIET])
    step = Path(base["outputs"]["step"])
    accepted_bytes = step.read_bytes()
    original = versioning.commit_version

    def select_new_experiment(*args, **kwargs):
        ChangeStore(isolated_dir).begin(
            command="run",
            label="newer",
            source=b"saved candidate",
            source_name="new.py",
            model={},
            promotion_mode="staged",
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(versioning, "commit_version", select_new_experiment)
    blocked = invoke(runner, ["run", "part.py", "--label", "older", *QUIET], 1)
    assert blocked["status"] != "success"
    manifest = json.loads((isolated_dir / "agentcad.json").read_text())
    assert manifest["current"] == "accepted"
    assert len(manifest["versions"]) == 1
    assert step.read_bytes() == accepted_bytes
