"""Milestone 2 is useful without opt-in, including registered MCP delivery."""

import asyncio
import base64
import hashlib
import json
from pathlib import Path
import shlex

import pytest

from agentcad.cli import cli
from agentcad.mcp import server


def invoke(runner, args):
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, (result.stdout, result.exception)
    return json.loads(result.stdout)


def unpack(result):
    if hasattr(result, "structuredContent") and result.structuredContent:
        return result.structuredContent
    if isinstance(result, dict):
        return result
    return json.loads(result[0].text)


def test_default_import_lookup_and_recovery_need_no_acknowledgment(runner, isolated_dir):
    invoke(runner, ["init", "--name", "default-inspect"])
    script = isolated_dir / "part.py"
    script.write_text("show_object(Box(60,30,8)-Cylinder(3,16))\n")
    built = invoke(runner, ["run", "part.py", "--label", "source", "--no-view", "--no-preview", "--no-diff", "--no-daemon"])
    assert built["geometry_manifest"]
    assert "experimental_inspect" not in built
    imported = invoke(runner, ["import", built["outputs"]["step"], "--label", "baseline", "--no-view", "--no-diff", "--no-daemon"])
    assert imported["core"]["status"] == "success"
    assert imported["inspection"]["media"] and imported["response_snapshot"]
    assert "geometry = load_geometry(" in (isolated_dir / "edit.py").read_text()
    assert "experimental_inspect" not in imported
    assert "--experimental-inspect" not in imported["response_snapshot"]["retrieve"]
    step = imported["outputs"]["step"]
    lookup = invoke(runner, ["inspect", step, "--id", "face_v2_1", "--no-daemon"])
    assert lookup["entity"]["id"] == "face_v2_1" and lookup["selector"]
    assert all("--experimental-inspect" not in action for action in lookup["next_actions"])
    restored = invoke(runner, [*shlex.split(imported["response_snapshot"]["retrieve"])[1:], "--no-daemon"])
    assert restored["core"]["status"] == "success" and restored["version"] == 2
    assert Path(restored["scaffold"]) == isolated_dir / "edit.py"
    assert "edit.py" in restored["next_actions"][0]


@pytest.mark.parametrize("compatibility", [None, False, True])
def test_registered_mcp_images_are_default_and_compatibility_flag_is_ignored(runner, isolated_dir, compatibility):
    from pathlib import Path

    source = Path(__file__).parents[1] / "tests/fixtures/real_world/cadgenbench_101_agentcad.step"
    args = {"file": str(source), "cwd": str(isolated_dir), "initialize": True,
            "view": False, "diff": False, "max_images": 1, "full_response": True}
    if compatibility is not None:
        args["experimental_inspect"] = compatibility
    result = asyncio.run(server.mcp.call_tool("import_cad", args))
    data = unpack(result)
    assert data["_exit_code"] == 0 and data["core"]["status"] == "success"
    assert data["inspection"]["counts"]["faces"] == 150
    assert "experimental_inspect" not in data
    assert data["inspection"]["inventory"]["retrieve"] == {"artifact": "manifest"}
    images = [block for block in result.content if block.type == "image"]
    assert len(images) == 1
    assert hashlib.sha256(base64.b64decode(images[0].data)).hexdigest() == data["inspection"]["media"][0]["sha256"]
    assert data["inspection"]["recognition"]["status"] == "not_available"
    assert data["inspection"]["recognition"]["recognized_feature_count"] is None


def test_milestone_two_does_not_enable_experimental_changes(runner, isolated_dir):
    before = sorted(isolated_dir.iterdir())
    result = runner.invoke(cli, ["run", "missing.py", "--label", "staged", "--stage", "--no-daemon"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["reason"] == "experimental_changes_required"
    assert sorted(isolated_dir.iterdir()) == before
