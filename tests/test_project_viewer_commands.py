"""Build success and live-viewer availability are independent CLI outcomes."""
import json

import pytest

from agentcad.cli import cli
from agentcad import project_viewer as live


@pytest.fixture
def project(runner, isolated_dir, monkeypatch):
    monkeypatch.setenv("AGENTCAD_VIEWER_HOME", str(isolated_dir / "runtime"))
    assert runner.invoke(cli, ["init", "--name", "live"]).exit_code == 0
    (isolated_dir / "model.py").write_text(
        'show_object(Box(40, 30, 5) - Cylinder(3, 10), id="plate")'
    )
    yield isolated_dir
    live.stop_service()


def build(runner, *extra):
    result = runner.invoke(cli, ["run", "model.py", "--label", "part", "--no-preview", "--no-diff", "--no-daemon", *extra])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.mark.parametrize("command", ["run", "import"])
def test_service_failure_does_not_lose_successful_geometry(command, runner, project, monkeypatch):
    def unavailable():
        raise live.ViewerUnavailable("Saved port is occupied")
    monkeypatch.setattr(live, "ensure_service", unavailable)
    payload = build(runner)
    if command == "import":
        result = runner.invoke(cli, ["import", payload["outputs"]["step"], "--label", "baseline", "--no-diff", "--no-daemon"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert (project / payload["outputs"]["step"]).is_file()
    assert (project / payload["viewer"]).is_file()
    assert payload["artifacts"]["viewer"]["status"] == "success"
    assert payload["project_viewer"]["status"] == "unavailable"
    assert "occupied" in payload["project_viewer"]["message"]
    assert payload["viewer_opened"] is False


def test_no_view_and_dry_run_do_not_start_service(runner, project):
    payload = build(runner, "--no-view")
    assert "viewer" not in payload  # core-only fast path
    assert "project_viewer" not in payload
    assert not live.service_status()["running"]
    payload = build(runner, "--no-view", "--preview")
    assert payload["viewer"]
    assert payload["viewer_opened"] is False
    assert not live.service_status()["running"]
    opened = runner.invoke(cli, ["viewer", "open"])
    assert opened.exit_code == 0, opened.output
    url = json.loads(opened.stdout)["url"]
    stopped = runner.invoke(cli, ["viewer", "stop"])
    assert stopped.exit_code == 0
    before = live.read_json(live.project_record(live.project_token(project)))
    build(runner, "--dry-run")
    assert live.read_json(live.project_record(live.project_token(project))) == before
    assert not live.service_status()["running"]
    reopened = runner.invoke(cli, ["viewer", "open"])
    assert json.loads(reopened.stdout)["url"] == url


def test_failed_builds_record_status_but_preserve_snapshot(runner, project, monkeypatch):
    payload = build(runner)
    record_path = live.project_record(live.project_token(project))
    latest = live.read_json(record_path)["latest"]
    (project / "model.py").write_text("broken syntax !")
    result = runner.invoke(cli, ["run", "model.py", "--label", "broken", "--no-daemon"])
    assert result.exit_code != 0
    assert live.read_json(record_path)["attempt"]["label"] == "broken"
    assert live.read_json(record_path)["latest"] == latest
    # Import can fail before it emits JSON. Preserve the original error, but
    # still tell an existing live page that its next attempted build failed.
    def crash(**kwargs):
        raise RuntimeError("native import failed")
    monkeypatch.setattr(cli.commands["import"], "callback", crash)
    result = runner.invoke(cli, ["import", payload["outputs"]["step"]])
    assert result.exit_code != 0
    assert live.read_json(record_path)["attempt"]["status"] == "failed"
    assert live.read_json(record_path)["latest"] == latest
