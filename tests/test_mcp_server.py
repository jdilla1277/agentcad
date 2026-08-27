"""Tests for agentcad MCP server."""

import json
import os

import pytest

from agentcad.mcp import server
from agentcad.mcp.server import (
    mcp,
    _invoke,
    _format_result,
    check_spec,
)


# --- Tool registration ---


EXPECTED_TOOLS = {
    "run", "render", "export", "measure", "inspect", "check_spec",
    "docs", "context", "recover", "diff", "view",
}


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


def test_docs_mcp_accepts_runtime_override(monkeypatch):
    calls = []

    def fake_invoke(args, cwd=None):
        calls.append((args, cwd))
        return {"status": "success", "runtime": "cadquery"}

    monkeypatch.setattr(server, "_invoke", fake_invoke)
    result = server.docs("preamble", "cadquery")

    assert result["runtime"] == "cadquery"
    assert calls == [(
        ["docs", "preamble", "--runtime", "cadquery"],
        None,
    )]


def test_run_mcp_passes_core_only_fast_path(monkeypatch):
    calls = []

    def fake_invoke(args, cwd=None):
        calls.append((args, cwd))
        return {"status": "success"}

    monkeypatch.setattr(server, "_invoke", fake_invoke)
    result = server.run(
        "part.py", "fast", "/tmp/project",
        preview=False, diff=False, view=False,
    )

    assert result["status"] == "success"
    assert calls == [(
        [
            "run", "part.py", "--label", "fast",
            "--no-preview", "--no-diff", "--no-view",
        ],
        "/tmp/project",
    )]


def test_docs_mcp_runtime_override_returns_cadquery_content():
    result = server.docs("quickstart", "cadquery")

    assert result["status"] == "success"
    assert result["runtime"] == "cadquery"
    assert "cq.Workplane" in result["content"]


def test_mcp_descriptions_are_build123d_forward():
    assert "build123d" in server.run.__doc__
    assert "build123d or CadQuery" not in server.run.__doc__
    assert "--runtime cadquery" in server.docs.__doc__


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


def test_recover_tool_passes_explicit_current_choice(monkeypatch):
    calls = []

    def fake_invoke(args, cwd=None):
        calls.append((args, cwd))
        return {"command": "recover", "status": "success"}

    monkeypatch.setattr(server, "_invoke", fake_invoke)
    result = server.recover("v3_interrupted", "/tmp/project", True)

    assert result["status"] == "success"
    assert calls == [(
        ["recover", "v3_interrupted", "--make-current"],
        "/tmp/project",
    )]


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


def test_measure_tool_missing_file_error():
    result = _invoke(["measure", "/tmp/nonexistent.step"])
    assert result["_exit_code"] != 0
    assert result["command"] == "measure"


def test_check_spec_tool_missing_file_error(tmp_path):
    """The check_spec MCP tool routes to `agentcad check-spec` and surfaces
    its structured error for a missing STEP file (a valid spec is present, so
    this reaches the file check rather than a spec-load error)."""
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps({"features": [{"name": "hole", "type": "cylinder",
                                  "diameter_mm": 6, "count": 1}]})
    )
    result = check_spec(str(tmp_path / "nonexistent.step"), str(spec), str(tmp_path))
    assert result["_exit_code"] != 0
    assert result["command"] == "check-spec"
    assert "not found" in result.get("message", "").lower()


def test_format_result_surfaces_exception_traceback():
    """When Click catches an unexpected exception, _invoke must surface the
    exception type and message instead of collapsing to 'No output' — that
    opacity was reported via feedback (2026-05-30) as hiding the traceback."""
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
    assert "measure" in result["content"]
