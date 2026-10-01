"""Render argument errors must precede recovery, routing, and CAD imports."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from agentcad.view_spec import ALL_VIEWS, NAMED_VIEWS, parse_view_spec


@pytest.fixture
def recorded_step(isolated_dir):
    version = isolated_dir / "v1_first"
    version.mkdir()
    step = version / "output.step"
    shutil.copyfile(Path(__file__).parent / "fixtures/comparison/box.step", step)
    (version / "meta.json").write_text(json.dumps({
        "status": "success", "outputs": {"step": "v1_first/output.step"},
    }))
    (isolated_dir / "agentcad.json").write_text(json.dumps({
        "name": "validation", "runtime": "build123d", "current": "first",
        "versions": [{"version": 1, "label": "first", "status": "success", "path": "v1_first/"}],
    }))
    return step


@pytest.mark.parametrize("options, expected", [
    (["--view", "iso", "--no-fit"], "--no-fit requires --focus"),
    (["--view", "iso", "--focus", "bad"], "Invalid --focus"),
    (["--view", "iso", "--focus", "1,2,bad"], "Invalid --focus"),
    (["--view", "iso", "--focus", ""], "Invalid --focus"),
    (["--view", "not-a-view"], "Invalid view spec"),
    (["--view", "front,45:bad"], "Invalid angle spec"),
    (["--view", "all", "--name", "detail"], "--name cannot be used with multiple views"),
    (["--view", "front,top", "--name", "detail"], "--name cannot be used with multiple views"),
    (["--view", "front,45:30", "--name", "detail"], "--name cannot be used with multiple views"),
])
@pytest.mark.parametrize("missing", [False, True], ids=["existing-step", "missing-step"])
@pytest.mark.parametrize("no_daemon", [False, True], ids=["daemon-allowed", "no-daemon"])
def test_render_input_errors_precede_recovery_routing_and_imports(
    isolated_dir, recorded_step, options, expected, missing, no_daemon
):
    args = ["render", "missing.step" if missing else str(recorded_step), *options]
    if no_daemon:
        args.append("--no-daemon")
    # A fresh interpreter catches even import attempts during CLI registration;
    # tests elsewhere in this suite may already have loaded the CAD libraries.
    probe = f'''
import importlib
import importlib.abc
import json
import shlex
import sys

class NoCAD(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {{'OCP', 'build123d', 'cadquery'}} or fullname == 'agentcad.render':
            raise AssertionError('Imported CAD/renderer: ' + fullname)

sys.meta_path.insert(0, NoCAD())
from click.testing import CliRunner
from agentcad.cli import cli
render = importlib.import_module('agentcad.commands.render')

def forbidden(*args, **kwargs):
    raise AssertionError('Reached missing-path recovery or daemon routing/startup')

render.missing_step_payload = forbidden
render.maybe_route_through_daemon = forbidden
render.maybe_spawn_daemon_for_next_run = forbidden
result = CliRunner().invoke(cli, {args!r})
assert result.exit_code == 1, (result.output, result.exception)
payload = json.loads(result.stdout)
assert payload['command'] == 'render' and payload['status'] == 'error', payload
assert {expected!r} in payload['message'], payload
assert payload['next_actions'] == ['agentcad render --help'], payload
for action in payload['next_actions']:
    followed = CliRunner().invoke(cli, shlex.split(action)[1:])
    assert followed.exit_code == 0, (followed.output, followed.exception)
assert not any(name.split('.')[0] in {{'OCP', 'build123d', 'cadquery'}} for name in sys.modules)
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=isolated_dir, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list(isolated_dir.rglob("*.png"))
    assert json.loads((isolated_dir / "agentcad.json").read_text())["current"] == "first"


@pytest.mark.parametrize("spec, expected", [
    ("iso", [("named", "iso")]),
    ("front,top", [("named", "front"), ("named", "top")]),
    ("all", [("named", name) for name in ALL_VIEWS]),
    ("-45,30", [("custom", (-45.0, 30.0))]),
    ("-45:30", [("custom", (-45.0, 30.0))]),
    ("front, -45:30", [("named", "front"), ("custom", (-45.0, 30.0))]),
])
def test_lightweight_parser_preserves_view_syntax(spec, expected):
    assert parse_view_spec(spec) == expected


def test_renderer_reexports_lightweight_parser_and_matches_view_names():
    from agentcad import render
    assert render.parse_view_spec is parse_view_spec
    assert render.NAMED_VIEWS == set(render.VIEWS) == NAMED_VIEWS
    assert render.ALL_VIEWS == ALL_VIEWS
