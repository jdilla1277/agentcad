"""The default CAD path stays ordinary; opted-in Inspect remains usable."""

import asyncio
import base64
import hashlib
import json
from pathlib import Path
import shlex

from agentcad.cli import cli
from agentcad.mcp import server
import pytest

FLAG = "--experimental-inspect"
QUIET = ["--no-preview", "--no-diff", "--no-view", "--no-daemon"]


def invoke(runner, args, code=0):
    result = runner.invoke(cli, args)
    assert result.exit_code == code, (result.stdout, result.exception)
    return json.loads(result.stdout)


def unpack(result):
    if isinstance(result, dict):
        return result
    if hasattr(result, "structuredContent") and result.structuredContent:
        return result.structuredContent
    return json.loads(result[0].text if isinstance(result, list) else result.content[0].text)


def test_ordinary_and_opted_build_import_retry_and_dispatch(runner, isolated_dir):
    invoke(runner, ["init", "--name", "gate"])
    (isolated_dir / "part.py").write_text("show_object(Box(20,30,6)-Cylinder(2,10))\n")
    built = invoke(runner, ["run", "part.py", "--label", "ordinary", *QUIET])
    assert "geometry_manifest" not in built and "experimental_inspect" not in built
    step = isolated_dir / built["outputs"]["step"]
    assert not step.with_name("geometry_manifest.json").exists()
    plain_args = ["import", str(step), "--label", "ordinary-import", "--idempotency-key", "plain", "--no-view", "--no-diff", "--no-daemon"]
    imported = invoke(runner, plain_args)
    assert "inspection" not in imported and "inspection_job" not in imported and "delivery" not in imported
    assert "geometry_manifest" not in imported and "load_geometry" not in (isolated_dir / "edit.py").read_text()
    assert invoke(runner, plain_args)["version"] == imported["version"]
    assert not (isolated_dir / ".agentcad/inspection_delivery").exists()
    advanced = invoke(runner, ["run", "part.py", "--label", "opted", FLAG, *QUIET])
    assert advanced["experimental_inspect"]["status"] == "work_in_progress"
    step = isolated_dir / advanced["outputs"]["step"]
    assert step.with_name("geometry_manifest.json").exists()
    assert all(FLAG in a for a in advanced["next_actions"])
    copied = invoke(runner, ["run", str(step), "--label", "dispatch", FLAG, *QUIET])
    assert copied["experimental_inspect"]["enabled"] and copied["inspection"]
    ordinary = invoke(runner, ["run", str(step), "--label", "plain-dispatch", *QUIET])
    assert "inspection_job" not in ordinary and "geometry_manifest" not in ordinary
    assert len(json.loads((isolated_dir / "agentcad.json").read_text())["versions"]) == 5


def test_registered_mcp_chunks_flag_recovery_and_no_opt_in_leak(runner, isolated_dir):
    invoke(runner, ["init", "--name", "mcp-gate"])
    (isolated_dir / "part.py").write_text("show_object(Box(20,30,6))\n")
    built = invoke(runner, ["run", "part.py", "--label", "ids", FLAG, *QUIET])
    step = isolated_dir / built["outputs"]["step"]
    args = {"file": str(step), "cwd": str(isolated_dir), "artifact": "manifest", "artifact_bytes": 128, "experimental_inspect": True}
    first = unpack(asyncio.run(server.mcp.call_tool("inspect", args)))
    assert first["experimental_inspect"]["status"] == "work_in_progress"
    assert FLAG in first["next"]["cli"]
    follow = first["next"]["mcp"]["arguments"]
    assert follow["experimental_inspect"] is True
    second = unpack(asyncio.run(server.mcp.call_tool("inspect", follow)))
    raw = step.with_name("geometry_manifest.json").read_bytes()
    assert base64.b64decode(second["artifact"]["data"]) == raw[128:256]
    assert second["artifact"]["sha256"] == hashlib.sha256(raw).hexdigest()
    denied = unpack(asyncio.run(server.mcp.call_tool("inspect", {**args, "experimental_inspect": False})))
    assert denied["reason"] == "experimental_inspect_required"
    plain = unpack(asyncio.run(server.mcp.call_tool("inspect", {"file": str(step), "cwd": str(isolated_dir), "ids": True})))
    assert plain["status"] == "success" and "experimental_inspect" not in plain
    assert "geometry_manifest" not in plain
    assert all("object_id" not in row for group in ("solids", "faces", "edges") for row in plain[group])


def test_experimental_import_retry_requires_flag_even_when_source_removed(runner, isolated_dir):
    invoke(runner, ["init", "--name", "saved"])
    (isolated_dir / "part.py").write_text("show_object(Box(10,20,5))\n")
    built = invoke(runner, ["run", "part.py", "--label", "source", *QUIET])
    source = isolated_dir / built["outputs"]["step"]
    args = ["import", str(source), "--label", "experiment", "--idempotency-key", "test", "--no-view", "--no-diff", "--no-daemon"]
    result = invoke(runner, [*args, FLAG])
    baseline = isolated_dir / result["outputs"]["step"]
    old = baseline.read_bytes()
    source.unlink()
    denied = invoke(runner, args, 1)
    assert denied["reason"] == "experimental_inspect_required"
    assert baseline.read_bytes() == old


def test_daemon_requests_do_not_inherit_opt_in(runner, isolated_dir):
    from agentcad.daemon import DaemonServer, daemon_supported
    if not daemon_supported():
        pytest.skip("POSIX daemon support required")
    invoke(runner, ["init", "--name", "daemon-gate"])
    (isolated_dir / "part.py").write_text("show_object(Box(10,20,5))\n")
    daemon = DaemonServer()
    def request(args):
        result = daemon.handle_request({"type": "run", "cwd": str(isolated_dir), "argv": args})
        return result, json.loads(result["output"])
    opted, data = request(["run", "part.py", "--label", "with-ids", FLAG, *QUIET])
    assert opted["exit_code"] == 0 and data["geometry_manifest"]
    refused, payload = request(["inspect", data["outputs"]["step"], "--report", "--no-daemon"])
    assert refused["exit_code"] == 1 and payload["reason"] == "experimental_inspect_required"
    plain, payload = request(["run", "part.py", "--label", "ordinary-again", *QUIET])
    assert plain["exit_code"] == 0 and "geometry_manifest" not in payload and "experimental_inspect" not in payload
