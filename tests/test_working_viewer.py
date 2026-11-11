"""Experimental working geometry, accepted delivery and pinned reviews differ."""

# collect-on-default-profile

import json
import asyncio
from pathlib import Path
from urllib.request import urlopen
from urllib.error import HTTPError

import pytest

from agentcad.cli import cli
from agentcad import project_viewer as live

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


def read(url):
    with urlopen(url, timeout=10) as response:
        return json.load(response)


@pytest.fixture
def project(runner, isolated_dir, monkeypatch):
    monkeypatch.setenv("AGENTCAD_VIEWER_HOME", str(isolated_dir / "runtime"))
    invoke(runner, ["init", "--name", "working-view"])
    (isolated_dir / "model.py").write_text(SOURCE)
    yield isolated_dir
    live.stop_service()


def run(runner, *extra, code=0):
    return invoke(
        runner, ["run", "model.py", "--label", "bracket", FLAG, *QUIET, *extra], code
    )


def open_viewer(runner):
    return invoke(runner, ["viewer", "open", FLAG])["url"]


def test_failed_geometry_follows_working_and_no_output_labels_older_model(
    runner, project
):
    accepted = run(runner)
    assert (
        accepted["project_viewer"]["revision_id"] == accepted["change"]["revision_id"]
    )
    assert not live.service_status()["running"]
    url = open_viewer(runner)
    initial = read(url + "state")
    assert initial["selected"]["status"] == "Accepted"
    (project / "checks.json").write_text(
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
    manifest = (project / "agentcad.json").read_bytes()
    failed = run(runner, "--stage", "--checks", "checks.json", code=1)
    state = read(url + "state")
    assert state["working"]["revision_id"] == failed["change"]["revision_id"]
    assert state["displayed"]["revision_id"] == failed["change"]["revision_id"]
    assert state["selected"]["status"] == "Failed"
    assert state["accepted"]["revision_id"] == accepted["change"]["revision_id"]
    assert state["older_geometry"] is False
    assert (project / "agentcad.json").read_bytes() == manifest
    evidence = read(url + "evidence/" + failed["change"]["revision_id"] + ".json")
    assert evidence["checks"]["validation_state"] == "invalid"
    assert (
        evidence["comparison"]["candidate"]["revision_id"]
        == failed["change"]["revision_id"]
    )
    (project / "model.py").write_text("value=1/int('0')\n" + SOURCE)
    broken = run(runner, "--stage", code=1)
    state = read(url + "state")
    assert state["working"]["revision_id"] == broken["change"]["revision_id"]
    assert state["displayed"]["revision_id"] == failed["change"]["revision_id"]
    assert state["selected"]["geometry_available"] is False
    assert state["older_geometry"] is True
    assert "division" in json.dumps(
        read(url + "evidence/" + broken["change"]["revision_id"] + ".json")
    )


def test_pin_and_live_check_status_survive_repair_restore_and_promotion(
    runner, project
):
    accepted = run(runner)
    original = accepted["change"]["revision_id"]
    url = open_viewer(runner)
    pinned = url + "state?revision=" + original
    (project / "model.py").write_text(SOURCE.replace("length=60", "length=80"))
    staged = run(runner, "--stage")
    revision = staged["change"]["revision_id"]
    assert read(url + "state")["selected"]["status"] == "Staged"
    assert read(pinned)["displayed"]["revision_id"] == original
    invoke(runner, ["promote", revision, "--idempotency-key", "reviewed", FLAG])
    assert read(url + "state")["selected"]["status"] == "Accepted"
    assert read(pinned)["displayed"]["revision_id"] == original
    assert read(pinned)["accepted"]["revision_id"] == revision
    assert read(pinned)["selected"]["status"] == "Previously accepted"
    pin_status = invoke(runner, ["viewer", "status", "--revision", original, FLAG])
    assert pin_status["selected"]["revision_id"] == original
    assert pin_status["working"]["revision_id"] == revision
    restored = invoke(
        runner,
        [
            "changes",
            "restore",
            original,
            "--label",
            "restored",
            "--idempotency-key",
            "restore:1",
            FLAG,
        ],
    )
    restored_id = restored["change"]["revision_id"]
    assert read(url + "state")["displayed"]["revision_id"] == restored_id
    repaired = invoke(
        runner,
        [
            "changes",
            "repair",
            restored_id,
            "model.py",
            "--label",
            "repaired",
            "--idempotency-key",
            "repair:1",
            FLAG,
        ],
    )
    assert (
        read(url + "state")["displayed"]["revision_id"]
        == repaired["change"]["revision_id"]
    )
    assert read(pinned)["displayed"]["revision_id"] == original
    viewed = invoke(runner, ["view", original, FLAG])
    assert viewed["project_viewer"]["pinned_revision_id"] == original
    assert "revision=" + original in viewed["project_viewer"]["url"]


def test_viewer_requires_explicit_wip_opt_in_and_status_matches_human(runner, project):
    accepted = run(runner)
    denied = invoke(runner, ["viewer", "open"], 1)
    assert denied["reason"] == "experimental_changes_required"
    status = invoke(runner, ["viewer", "status", FLAG])
    assert status["working"]["revision_id"] == accepted["change"]["revision_id"]
    assert status["selected"]["status"] == "Accepted"


def test_late_prepared_snapshot_cannot_replace_newer_working_revision(runner, project):
    from agentcad.working_viewer import prepare

    older = run(runner)["change"]["revision_id"]
    newer = run(runner, "--stage")["change"]["revision_id"]
    url = open_viewer(runner)
    prepare(project, older)
    assert read(url + "state")["displayed"]["revision_id"] == newer


def test_checking_is_visible_before_execution_and_server_never_loads_cad(
    runner, project
):
    from agentcad.change_store import ChangeStore

    accepted = run(runner)["change"]["revision_id"]
    url = open_viewer(runner)
    attempt = ChangeStore(project).begin(
        command="run",
        label="building",
        source=SOURCE.encode(),
        source_name="model.py",
        model={"runtime": "build123d", "validation_profile": "deliverable"},
        promotion_mode="staged",
    )
    state = read(url + "state")
    assert state["selected"]["status"] == "Checking"
    assert state["working"]["revision_id"] == attempt.revision_id
    assert state["displayed"]["revision_id"] == accepted
    assert live.service_status()["cad_loaded"] is False


def test_status_history_and_execution_replay_do_not_write_viewer_state(runner, project):
    original = run(runner, "--idempotency-key", "viewer:1")
    url = open_viewer(runner)
    read(url + "state")

    def files():
        # The CLI audit log can record an invocation, including a retry. It is
        # separate from immutable revision and viewer state.
        return {
            str(p.relative_to(project)): p.read_bytes()
            for p in project.rglob("*")
            if p.is_file() and p != project / ".agentcad/session.jsonl"
        }

    before = files()
    invoke(runner, ["viewer", "status", FLAG])
    invoke(runner, ["changes", "show", original["change"]["revision_id"], FLAG])
    replay = run(runner, "--idempotency-key", "viewer:1")
    assert replay["change"]["revision_id"] == original["change"]["revision_id"]
    invoke(runner, ["run", "--idempotency-key", "viewer:1", "--status", FLAG])
    read(url + "state")
    read(url + "evidence/" + original["change"]["revision_id"] + ".json")
    assert files() == before


def test_render_failure_keeps_accepted_model_and_never_publishes_partial_media(
    runner, project, monkeypatch
):
    accepted = run(runner)["change"]["revision_id"]
    url = open_viewer(runner)
    manifest = (project / "agentcad.json").read_bytes()

    def fail(shape, path, *args, **kwargs):
        Path(path).write_bytes(b"incomplete mesh")
        raise RuntimeError("fixture mesh preparation failed")

    monkeypatch.setattr("agentcad.export.export_glb", fail)
    result = run(runner, "--stage")
    revision = result["change"]["revision_id"]
    assert result["status"] == "staged"
    assert result["project_viewer"]["status"] == "unavailable"
    state = read(url + "state")
    assert state["selected"]["status"] == "Staged"
    assert state["displayed"]["revision_id"] == accepted
    assert state["older_geometry"] is True
    assert "mesh preparation" in state["selected"]["message"]
    assert (project / "agentcad.json").read_bytes() == manifest
    record = live.read_json(live.project_record(live.project_token(project)))
    assert revision not in record["working_snapshots"]
    assert not list(
        (project / ".agentcad/working_viewer" / revision).glob("*/viewer.html")
    )


def test_only_registered_untampered_snapshots_are_served(runner, project):
    from agentcad.working_viewer import prepare

    accepted = run(runner)["change"]["revision_id"]
    result = run(runner, "--stage")
    revision = result["change"]["revision_id"]
    url = open_viewer(runner)
    state = read(url + "state")
    artifact_url = url + state["latest"]["artifact"]
    with urlopen(artifact_url) as response:
        assert b"change_receipt" in response.read()
    html = project / state["latest"]["html"]
    html.write_text("tampered")
    unavailable = read(url + "state")
    assert unavailable["selected"]["preview_state"] == "unavailable"
    assert unavailable["displayed"]["revision_id"] == accepted
    with pytest.raises(HTTPError) as exc:
        urlopen(artifact_url)
    assert exc.value.code == 503
    with pytest.raises(live.ViewerUnavailable):
        prepare(project, revision)
    for path in (
        "revisions/../../model.py",
        "evidence/../../model.py",
        "state?revision=accepted",
    ):
        with pytest.raises(HTTPError):
            urlopen(url + path)


def test_registered_mcp_viewer_has_boolean_gate_and_configured_project_state(
    runner, project
):
    from agentcad.mcp.server import mcp

    invoke(runner, ["init", "--name", "configured", "--build-dir", "outputs"])
    result = run(runner, "--build-dir", "outputs")

    async def call(name, args):
        value = await mcp.call_tool(name, args)
        return value[1] if isinstance(value, tuple) else json.loads(value[0].text)

    args = {
        "cwd": str(project),
        "action": "status",
        "build_dir": "outputs",
        "experimental_changes": True,
    }
    state = asyncio.run(call("viewer", args))
    assert state["selected"]["revision_id"] == result["change"]["revision_id"]
    assert state["build_root"] == str(project / "outputs")
    with pytest.raises(ValueError):
        asyncio.run(call("viewer", {**args, "experimental_changes": "true"}))
    with pytest.raises(ValueError):
        asyncio.run(call("viewer", {**args, "pin": "accepted"}))


def test_status_does_not_create_an_uninitialized_build_or_viewer_service(
    runner, isolated_dir, monkeypatch
):
    monkeypatch.setenv("AGENTCAD_VIEWER_HOME", str(isolated_dir / "unused-runtime"))
    status = invoke(runner, ["viewer", "status", "--build-dir", "missing-build", FLAG])
    assert status["running"] is False
    assert status["selected"]["status"] == "Unavailable"
    assert not list(isolated_dir.iterdir())


def test_ordinary_build_returns_to_standard_follow_without_breaking_existing_pin(
    runner, project
):
    original = run(runner)["change"]["revision_id"]
    url = open_viewer(runner)
    ordinary = invoke(
        runner, ["run", "model.py", "--label", "ordinary", *QUIET, "--view"]
    )
    assert "change" not in ordinary
    state = read(url + "state")
    assert "experimental_changes" not in state
    assert state["latest"]["version"] == ordinary["version"]
    pinned = read(url + "state?revision=" + original)
    assert pinned["displayed"]["revision_id"] == original
    assert pinned["accepted"]["revision_id"] is None
    assert pinned["accepted"]["version"] == ordinary["version"]
