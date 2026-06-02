"""Integration tests: verify CLI commands auto-log to session.jsonl."""

import json
from pathlib import Path

from click.testing import CliRunner
from agentcad.cli import cli


def test_init_command_is_logged(runner, isolated_dir):
    result = runner.invoke(cli, ["init", "--name", "testproj"])
    assert result.exit_code == 0

    log_file = isolated_dir / ".agentcad" / "session.jsonl"
    assert log_file.exists()

    entries = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
    assert len(entries) == 1
    assert entries[0]["command"] == "init"
    assert entries[0]["result"]["status"] == "success"


def test_context_command_is_logged(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "testproj"])
    result = runner.invoke(cli, ["context"])
    assert result.exit_code == 0

    log_file = isolated_dir / ".agentcad" / "session.jsonl"
    entries = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
    assert len(entries) == 2
    assert entries[1]["command"] == "context"


def test_feedback_command_is_not_logged(runner, isolated_dir):
    """The feedback command itself should not be logged to prevent recursion."""
    runner.invoke(cli, ["feedback", "test message"])

    log_file = isolated_dir / ".agentcad" / "session.jsonl"
    if log_file.exists():
        entries = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
        assert not any(e["command"] == "feedback" for e in entries)


def test_logged_entry_has_args(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "myproj"])

    log_file = isolated_dir / ".agentcad" / "session.jsonl"
    entries = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
    assert entries[0]["args"]["argv"] is not None


def test_feedback_reads_auto_logged_session(runner, isolated_dir):
    """End-to-end: run commands, then feedback bundles them."""
    runner.invoke(cli, ["init", "--name", "testproj"])
    runner.invoke(cli, ["context"])

    result = runner.invoke(cli, ["feedback", "init was smooth"])
    assert result.exit_code == 0

    output = json.loads(result.stdout)
    # Should include the 2 auto-logged entries
    assert len(output["bundle"]["session_log"]) == 2
    assert output["bundle"]["session_log"][0]["command"] == "init"
    assert output["bundle"]["session_log"][1]["command"] == "context"
