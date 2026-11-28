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


def test_cli_logs_actual_invocation_args(runner, isolated_dir, monkeypatch):
    import sys

    monkeypatch.setattr(sys, "argv", ["unrelated-server", "stale-command", "stale-arg"])
    result = runner.invoke(cli, ["init", "--name", "project with spaces"])
    assert result.exit_code == 0, result.output
    entry = json.loads((isolated_dir / ".agentcad/session.jsonl").read_text().splitlines()[0])
    assert entry["args"]["argv"] == ["--name", "project with spaces"]
    assert sys.argv == ["unrelated-server", "stale-command", "stale-arg"]


def test_mcp_failed_runs_are_logged_once_with_request_args(runner, isolated_dir, monkeypatch):
    from agentcad.mcp.server import run
    from agentcad.session_log import SessionLogger

    monkeypatch.delenv("AGENTCAD_NO_LOG", raising=False)
    assert runner.invoke(cli, ["init", "--name", "mcp-logging"]).exit_code == 0
    for label in ("first", "second"):
        response = run(f"{label}.py", cwd=str(isolated_dir), output=label,
                       preview=False, diff=False, view=False)
        assert response["_exit_code"] == 1
    logger = SessionLogger(isolated_dir)
    entries = logger.get_recent(10)
    runs = [entry for entry in entries if entry["command"] == "run"]
    assert len(runs) == 2
    for entry, label in zip(runs, ("first", "second")):
        assert entry["args"]["argv"] == [f"{label}.py", "--label", label,
                                          "--no-preview", "--no-diff", "--no-view"]
        assert entry["result"]["status"] == "error"
    bundle = logger.get_session_bundle("failed MCP runs")
    assert bundle["friction_signals"]["errors"] == 2
    assert bundle["friction_signals"]["total_commands"] == 3


def test_mcp_respects_explicit_logging_opt_out(runner, isolated_dir, monkeypatch):
    from agentcad.mcp.server import _invoke

    monkeypatch.setenv("AGENTCAD_NO_LOG", "1")
    assert runner.invoke(cli, ["init", "--name", "no-log"]).exit_code == 0
    response = _invoke(["context"], cwd=str(isolated_dir))
    assert response["_exit_code"] == 0
    assert not (isolated_dir / ".agentcad/session.jsonl").exists()


def test_stdio_mcp_logs_failed_run(runner, isolated_dir, monkeypatch):
    import os
    import sys

    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from agentcad.session_log import SessionLogger

    monkeypatch.delenv("AGENTCAD_NO_LOG", raising=False)
    assert runner.invoke(cli, ["init", "--name", "stdio-logging"]).exit_code == 0
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "agentcad.mcp"],
        env=dict(os.environ),
    )

    async def exercise():
        with anyio.fail_after(30):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("run", {
                        "script": "missing.py", "cwd": str(isolated_dir), "output": "bad",
                        "preview": False, "diff": False, "view": False,
                    })
                    return json.loads(result.content[0].text)

    response = anyio.run(exercise)
    assert response["_exit_code"] == 1, response
    logger = SessionLogger(isolated_dir)
    entries = logger.get_recent(10)
    assert [entry["command"] for entry in entries] == ["init", "run"]
    assert entries[1]["args"]["argv"] == ["missing.py", "--label", "bad",
                                             "--no-preview", "--no-diff", "--no-view"]
    assert logger.friction_signals()["errors"] == 1
