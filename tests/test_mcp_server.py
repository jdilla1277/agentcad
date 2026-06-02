"""Tests for agentcad MCP server."""

import json
import os

import pytest

from agentcad.mcp.server import mcp, _format_result, _invoke


# --- Tool registration ---


EXPECTED_TOOLS = {"run", "render", "export", "inspect", "docs", "context", "diff", "view"}


def _tool_names():
    return {t.name for t in mcp._tool_manager.list_tools()}


def test_mcp_server_has_all_tools():
    names = _tool_names()
    for tool in EXPECTED_TOOLS:
        assert tool in names, f"Missing tool: {tool}"


def test_mcp_no_daemon_tool():
    assert "daemon" not in _tool_names()


def test_mcp_no_feedback_tool():
    assert "feedback" not in _tool_names()


def test_mcp_no_init_tool():
    assert "init" not in _tool_names()


# --- _invoke helper ---


def test_invoke_docs_returns_json():
    result = _invoke(["docs", "quickstart"])
    assert result["command"] == "docs"
    assert result["status"] == "success"
    assert "content" in result


def test_invoke_error_returns_exit_code():
    result = _invoke(["context"])  # no manifest → error
    assert result["_exit_code"] != 0


def test_invoke_cwd_parameter(tmp_path, monkeypatch):
    """cwd parameter should change the working directory for the command."""
    from agentcad.cli import cli
    from click.testing import CliRunner

    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTCAD_DAEMON", "1")
    runner.invoke(cli, ["init", "--name", "test_project"])

    # Call from a different directory using cwd
    original = os.getcwd()
    result = _invoke(["context"], cwd=str(tmp_path))
    assert result["status"] == "success"
    # Verify cwd was restored
    assert os.getcwd() == original


# --- Tool response tests ---


def test_docs_tool_returns_content():
    result = _invoke(["docs", "quickstart"])
    assert result["status"] == "success"
    assert len(result["content"]) > 0


def test_context_tool_error_without_manifest():
    result = _invoke(["context"])
    assert result["status"] == "error"


def test_context_tool_success_with_project(tmp_path, monkeypatch):
    from agentcad.cli import cli
    from click.testing import CliRunner

    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTCAD_DAEMON", "1")
    runner.invoke(cli, ["init", "--name", "test_project"])

    result = _invoke(["context"], cwd=str(tmp_path))
    assert result["status"] == "success"
    assert result["command"] == "context"


def test_run_tool_missing_script_error(tmp_path, monkeypatch):
    from agentcad.cli import cli
    from click.testing import CliRunner

    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTCAD_DAEMON", "1")
    runner.invoke(cli, ["init", "--name", "test_project"])

    result = _invoke(["run", "nonexistent.py", "--output", "v1"], cwd=str(tmp_path))
    assert result["status"] == "error"
    assert "not found" in result.get("message", "").lower()


def test_inspect_tool_missing_file_error():
    result = _invoke(["inspect", "/tmp/nonexistent.step"])
    assert result["_exit_code"] != 0


def test_format_result_surfaces_exception_traceback():
    try:
        raise RuntimeError("kaboom on windows")
    except RuntimeError as exc:
        data = _format_result("", 1, exception=exc)

    assert data["status"] == "error"
    assert "kaboom on windows" in data["message"]
    assert "RuntimeError" in data["message"]
    assert data["_exit_code"] == 1


def test_format_result_no_output_without_exception():
    data = _format_result("", 1)
    assert data["message"] == "No output"
    assert data["_exit_code"] == 1


def test_format_result_parses_json_output():
    data = _format_result('{"status": "success", "command": "context"}', 0)
    assert data["status"] == "success"
    assert data["_exit_code"] == 0


def test_docs_mcp_section():
    result = _invoke(["docs", "mcp"])
    assert result["status"] == "success"
    assert ".mcp.json" in result["content"]
    assert "agentcad.mcp" in result["content"]
