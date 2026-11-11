"""Enriched Inspect requires acknowledgment on every public call."""

import asyncio
import json

import pytest

from agentcad.cli import cli
from agentcad.mcp import server

FLAG = "--experimental-inspect"


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() if p.is_file() else None for p in root.rglob("*")}


@pytest.mark.parametrize("args", [
    ["inspect", "missing.step", "--report"],
    ["inspect", "missing.step", "--id", "face_v1_1"],
    ["inspect", "missing.step", "--image"],
    ["inspect", "missing.step", "--artifact", "manifest"],
    ["inspect", "missing.step", "--report", "--job", "old", "--job-action", "cancel"],
    ["inspect", "missing.step", "--report", "--response-snapshot", "old"],
    ["import", "missing.step", "--init", "--report"],
    ["import", "missing.step", "--init", "--inspection-image"],
    ["import", "missing.step", "--init", "--inspection-background"],
    ["import", "missing.step", "--init", "--inspection-wait-timeout", "0"],
    ["import", "missing.step", "--response-budget", "1000"],
    ["import", "missing.step", "--max-images", "0"],
])
def test_refuses_before_source_read_or_project_writes(runner, isolated_dir, args):
    before = tree(isolated_dir)
    result = runner.invoke(cli, [*args, "--no-daemon"])
    assert result.exit_code == 1, result.stdout
    data = json.loads(result.stdout)
    assert data["reason"] == "experimental_inspect_required"
    assert FLAG in data["suggestion"]
    assert tree(isolated_dir) == before


@pytest.mark.parametrize("value", ["true", 1, None])
def test_mcp_requires_actual_boolean(value, isolated_dir):
    with pytest.raises(ValueError, match="experimental_inspect must be a boolean"):
        asyncio.run(server.mcp.call_tool("inspect", {"file": "missing.step", "cwd": str(isolated_dir),
                                                   "report": True, "experimental_inspect": value}))


def test_no_environment_or_previous_call_activation(runner, isolated_dir, monkeypatch):
    monkeypatch.setenv("AGENTCAD_EXPERIMENTAL_INSPECT", "1")
    (isolated_dir / "agentcad.toml").write_text('[inspection]\nexperimental_inspect=true\n')
    opted = runner.invoke(cli, ["inspect", "missing.step", "--report", FLAG, "--no-daemon"])
    assert json.loads(opted.stdout)["experimental_inspect"]["status"] == "work_in_progress"
    plain = runner.invoke(cli, ["inspect", "missing.step", "--report", "--no-daemon"])
    assert json.loads(plain.stdout)["reason"] == "experimental_inspect_required"
    basic = runner.invoke(cli, ["inspect", "missing.step", "--ids", "--no-daemon"])
    assert "experimental_inspect" not in json.loads(basic.stdout)


def test_discovery_identifies_wip_and_default_false():
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    for name in ("import_cad", "inspect", "run", "run_status"):
        prop = tools[name].inputSchema["properties"]["experimental_inspect"]
        assert prop["default"] is False
        assert "WIP" in prop["description"] and "every call" in tools[name].description


def test_sealed_records_not_rewritten_but_current_actions_are(runner, isolated_dir):
    from agentcad.experimental_inspect import present, option
    import click

    historical = {"kind": "evidence", "content_sha256": "saved", "next_actions": ["agentcad inspect old.step --report"]}
    @click.command()
    @option
    def example():
        click.echo(json.dumps(present({"command": "inspect", "historical": historical,
            "next": {"cli": "agentcad inspect old.step --artifact manifest", "mcp": {"tool": "inspect", "arguments": {"artifact": "manifest"}}}})))
    result = runner.invoke(example, [FLAG])
    data = json.loads(result.stdout)
    assert data["historical"] == historical
    assert FLAG in data["next"]["cli"]
    assert data["next"]["mcp"]["arguments"]["experimental_inspect"] is True


def test_quoted_em_dash_and_flag_in_explanation_remain_copyable(runner):
    from agentcad.experimental_inspect import option, present
    import click
    import shlex

    filename = "/supplier — owner's files/output.step"
    command = "agentcad inspect " + shlex.quote(filename) + " --image — repeat with --experimental-inspect"
    @click.command()
    @option
    def example():
        first = present({"command": "inspect", "next_actions": [command]})
        assert present(first) == first
        click.echo(json.dumps(first))
    result = runner.invoke(example, [FLAG])
    assert result.exit_code == 0, result.output
    action = json.loads(result.stdout)["next_actions"][0]
    core = action.rsplit(" — ", 1)[0]
    assert shlex.split(core) == ["agentcad", "inspect", filename, "--image", FLAG]
