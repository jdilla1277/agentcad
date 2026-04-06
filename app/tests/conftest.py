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
    """Prevent tests from routing through a running daemon process."""
    monkeypatch.setenv("AGENTCAD_DAEMON", "1")
