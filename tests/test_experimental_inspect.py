"""Deprecated Inspect acknowledgment is accepted without gating or rewriting evidence."""

import asyncio
import json

import pytest

from agentcad.cli import cli
from agentcad.mcp import server

FLAG = "--experimental-inspect"


@pytest.mark.parametrize("value", ["true", 1, None])
def test_mcp_compatibility_input_requires_actual_boolean(value, isolated_dir):
    with pytest.raises(ValueError, match="experimental_inspect must be a boolean"):
        asyncio.run(server.mcp.call_tool("inspect", {"file": "missing.step", "cwd": str(isolated_dir),
                                                   "report": True, "experimental_inspect": value}))


@pytest.mark.parametrize("args", [[], [FLAG]])
def test_inspect_reaches_file_validation_without_acknowledgment(runner, isolated_dir, args):
    result = runner.invoke(cli, ["inspect", "missing.step", "--report", *args, "--no-daemon"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data.get("reason") != "experimental_inspect_required"
    assert "experimental_inspect" not in data
    assert data["status"] == "error" and data["reason"] == "report_unavailable"
    assert not (isolated_dir / "agentcad.json").exists()


def test_discovery_marks_compatibility_input_deprecated_and_default_help_hides_it(runner):
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    for name in ("import_cad", "inspect", "run", "run_status"):
        prop = tools[name].inputSchema["properties"]["experimental_inspect"]
        assert prop["default"] is False and prop["deprecated"] is True
        assert "ignored" in prop["description"]
        assert "requires experimental_inspect" not in tools[name].description
    for command in ("import", "inspect", "run"):
        assert FLAG not in runner.invoke(cli, [command, "--help"]).stdout


def test_sealed_records_and_current_actions_are_not_rewritten(runner):
    from agentcad.experimental_inspect import present, option
    import click

    historical = {"kind": "evidence", "content_sha256": "saved", "next_actions": ["agentcad inspect old.step --report " + FLAG]}
    payload = {"command": "inspect", "historical": historical,
               "next": {"cli": "agentcad inspect old.step --artifact manifest",
                        "mcp": {"tool": "inspect", "arguments": {"artifact": "manifest"}}}}
    @click.command()
    @option
    def example():
        click.echo(json.dumps(present(payload)))
    for args in ([], [FLAG]):
        result = runner.invoke(example, args)
        assert json.loads(result.stdout) == payload


def test_quoted_em_dash_and_flag_in_explanation_remain_unchanged():
    from agentcad.experimental_inspect import present
    import shlex

    filename = "/supplier — owner's files/output.step"
    command = "agentcad inspect " + shlex.quote(filename) + " --image — repeat with --experimental-inspect"
    payload = {"command": "inspect", "next_actions": [command]}
    assert present(present(payload)) == payload
    assert shlex.split(command.rsplit(" — ", 1)[0]) == ["agentcad", "inspect", filename, "--image"]


def test_damaged_legacy_settings_fail_before_reading_source(runner, isolated_dir):
    import hashlib
    from agentcad.import_requests import _save

    request_id = "import_" + hashlib.sha256(b"old").hexdigest()
    record = isolated_dir / ".agentcad/import_requests" / request_id / "request.json"
    record.parent.mkdir(parents=True)
    _save(record, {"schema_version": 1, "request_id": request_id,
                   "semantic": {"experimental_inspect": False}})
    result = runner.invoke(cli, ["import", "missing.step", "--idempotency-key", "old", "--no-daemon"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["reason"] == "import_request_unavailable"
    assert "settings are missing or invalid" in payload["message"]
    assert not (isolated_dir / "agentcad.json").exists()
