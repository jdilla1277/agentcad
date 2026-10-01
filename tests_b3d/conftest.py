"""Shared fixtures for build123d runtime tests.

Mirrors tests/conftest.py so b3d integration tests run under the
same harness — no accidental daemon routing, no network calls to the
feedback endpoint, same `runner` / `isolated_dir` contract.
"""

import pytest
from click.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _no_daemon(monkeypatch):
    monkeypatch.setenv("AGENTCAD_DAEMON", "1")


@pytest.fixture(autouse=True)
def _no_remote_feedback(monkeypatch):
    from agentcad.commands import feedback as feedback_mod

    monkeypatch.setattr(feedback_mod, "_send_remote", lambda bundle: None)


@pytest.fixture(autouse=True)
def _no_browser_launch(monkeypatch):
    """Stub `webbrowser.open` so view/diff tests never spawn a real browser."""
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda _url: True)


# ---------- b3d-specific helpers ----------

SIMPLE_B3D_SCRIPT = """\
from build123d import Box
show_object(Box(10, 10, 10))
"""

MULTI_PART_B3D_SCRIPT = """\
from build123d import Box, Sphere, Compound
a = Box(10, 10, 10)
b = Sphere(6).translate((20, 0, 0))
show_object(Compound(children=[a, b]))
"""


@pytest.fixture
def b3d_project(runner, isolated_dir):
    """Initialized build123d-mode project. Yields the project dir."""
    result = runner.invoke(cli_app(), ["init", "--name", "test", "--runtime", "build123d"])
    assert result.exit_code == 0, result.output
    return isolated_dir


@pytest.fixture
def b3d_step(runner, b3d_project):
    """Run SIMPLE_B3D_SCRIPT, return path to produced STEP. Used as the
    input for downstream commands (export/render/inspect/view/diff)."""
    script = b3d_project / "script.py"
    script.write_text(SIMPLE_B3D_SCRIPT)
    result = runner.invoke(cli_app(), ["run", "script.py", "--output", "box", "--no-preview"])
    assert result.exit_code == 0, result.output
    return b3d_project / "v1_box" / "output.step"


def cli_app():
    from agentcad.cli import cli
    return cli
