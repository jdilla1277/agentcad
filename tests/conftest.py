from pathlib import Path

import pytest
from click.testing import CliRunner

# Real-world CAD fixtures live alongside this conftest. See
# fixtures/real_world/README.md for inventory and provenance.
_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "real_world"


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def real_world_step():
    """Path to the Pump Manifold v3 fixture — a real Fusion-exported AP214
    STEP. Use for tests that need real-world topology characteristics
    (118 faces, asymmetric face_orientations, AP214 schema) rather than
    cadquery-generated synthetic geometry."""
    p = _FIXTURES_DIR / "pump_manifold.step"
    if not p.exists():
        pytest.skip(f"real-world fixture missing: {p}")
    return p


@pytest.fixture
def isolated_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _no_daemon(monkeypatch):
    """Prevent tests from routing through a running daemon process."""
    monkeypatch.setenv("AGENTCAD_DAEMON", "1")


@pytest.fixture(autouse=True)
def _no_remote_feedback(monkeypatch):
    """Stub the feedback remote POST so pytest never hits the production endpoint."""
    from agentcad.commands import feedback as feedback_mod

    monkeypatch.setattr(
        feedback_mod,
        "_send_remote",
        lambda bundle: {"err": None, "discord": "ok", "neon_row_id": 0},
    )


@pytest.fixture(autouse=True)
def _no_remote_feedback_reads(monkeypatch):
    """Stub the feedback read API (GET/PATCH) so pytest never hits the production endpoint."""
    from agentcad.commands import feedback as feedback_mod

    monkeypatch.setattr(
        feedback_mod,
        "_call_read_api",
        lambda method, key, params=None, body=None: (None, {"items": [], "count": 0}),
    )


@pytest.fixture(autouse=True)
def _no_browser_launch(monkeypatch):
    """Stub `webbrowser.open` so view/diff tests never spawn a real browser."""
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda _url: True)


@pytest.fixture(autouse=True)
def _no_remote_subscribe(monkeypatch):
    """Stub the subscribe remote POST so pytest never hits the production endpoint."""
    from agentcad.commands import subscribe as subscribe_mod

    monkeypatch.setattr(
        subscribe_mod,
        "_send_remote",
        lambda payload: (None, {"status": "pending_confirmation"}),
    )
