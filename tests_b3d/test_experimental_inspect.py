"""Default Inspect preserves old retry identities and the separate changes gate."""

import asyncio
import base64
import hashlib
import json

import pytest

from agentcad.cli import cli
from agentcad.mcp import server

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


@pytest.mark.parametrize("dispatch", ["cli", "run", "mcp"])
def test_old_ordinary_import_retry_preserves_options_and_result(runner, isolated_dir, monkeypatch, dispatch):
    invoke(runner, ["init", "--name", "legacy"])
    (isolated_dir / "part.py").write_text("show_object(Box(20,30,6)-Cylinder(2,10))\n")
    built = invoke(runner, ["run", "part.py", "--label", "source", *QUIET])
    source = isolated_dir / built["outputs"]["step"]
    args = ["import", str(source), "--label", "legacy", "--idempotency-key", "old", "--no-view", "--no-diff", "--no-daemon"]
    # Reproduce the pre-graduation ordinary mode, including its semantic marker.
    with monkeypatch.context() as patch:
        patch.setattr("agentcad.experimental_inspect.enabled", lambda: False)
        patch.setattr("agentcad.commands.import_cmd.inspect_enabled", lambda: False)
        old = invoke(runner, [*args, "--no-report", "--no-inspection-image"])
    assert "inspection" not in old and "geometry_manifest" not in old
    baseline = isolated_dir / old["outputs"]["step"]
    protected = {p: p.read_bytes() for p in baseline.parent.rglob("*") if p.is_file()}
    if dispatch == "run":
        replay_args = ["run", str(source), "--label", "legacy", "--idempotency-key", "old", *QUIET]
    else:
        replay_args = args
    if dispatch == "mcp":
        replay = unpack(asyncio.run(server.mcp.call_tool("import_cad", {
            "file": str(source), "cwd": str(isolated_dir), "label": "legacy",
            "idempotency_key": "old", "view": False, "diff": False,
        })))
        assert replay["_exit_code"] == 0
    else:
        replay = invoke(runner, replay_args)
    assert replay["version"] == old["version"] and replay["idempotency"]["replayed"]
    assert "inspection" not in replay and "geometry_manifest" not in replay
    assert {p: p.read_bytes() for p in protected} == protected
    conflict = invoke(runner, [*args, "--report"], 1)
    assert conflict["reason"] == "idempotency_conflict"
    # No old invocation mode leaks into the next default request.
    fresh = invoke(runner, ["run", "part.py", "--label", "fresh", *QUIET])
    assert fresh["geometry_manifest"]


def test_mcp_chunks_and_numeric_ids_are_default(runner, isolated_dir):
    invoke(runner, ["init", "--name", "mcp-default"])
    (isolated_dir / "part.py").write_text("show_object(Box(20,30,6))\n")
    built = invoke(runner, ["run", "part.py", "--label", "ids", *QUIET])
    step = isolated_dir / built["outputs"]["step"]
    first = unpack(asyncio.run(server.mcp.call_tool("inspect", {
        "file": str(step), "cwd": str(isolated_dir), "artifact": "manifest", "artifact_bytes": 128})))
    assert "--experimental-inspect" not in first["next"]["cli"]
    follow = first["next"]["mcp"]["arguments"]
    assert "experimental_inspect" not in follow
    second = unpack(asyncio.run(server.mcp.call_tool("inspect", follow)))
    raw = step.with_name("geometry_manifest.json").read_bytes()
    assert base64.b64decode(second["artifact"]["data"]) == raw[128:256]
    assert second["artifact"]["sha256"] == hashlib.sha256(raw).hexdigest()
    plain = unpack(asyncio.run(server.mcp.call_tool("inspect", {"file": str(step), "cwd": str(isolated_dir), "ids": True})))
    assert plain["status"] == "success" and plain["geometry_manifest"]
    assert all(row["object_id"] for group in ("solids", "faces", "edges") for row in plain[group])


def test_opted_in_import_key_replays_without_flag(runner, isolated_dir, monkeypatch):
    invoke(runner, ["init", "--name", "saved"])
    (isolated_dir / "part.py").write_text("show_object(Box(10,20,5))\n")
    built = invoke(runner, ["run", "part.py", "--label", "source", *QUIET])
    source = isolated_dir / built["outputs"]["step"]
    args = ["import", str(source), "--label", "baseline", "--idempotency-key", "test", "--max-images", "0", "--no-view", "--no-diff", "--no-daemon"]
    first = invoke(runner, [*args, "--experimental-inspect"])
    def unexpected(*a, **kw):
        pytest.fail("Replay attempted new CAD work")
    monkeypatch.setattr("agentcad.commands.import_cmd._import_impl", unexpected)
    replay = invoke(runner, args)
    assert replay["version"] == first["version"] and replay["idempotency"]["replayed"]


def test_daemon_requests_do_not_retain_legacy_mode(runner, isolated_dir):
    from agentcad.daemon import DaemonServer, daemon_supported
    if not daemon_supported():
        pytest.skip("POSIX daemon support required")
    invoke(runner, ["init", "--name", "daemon-default"])
    (isolated_dir / "part.py").write_text("show_object(Box(10,20,5))\n")
    daemon = DaemonServer()
    for args in (["run", "part.py", "--label", "first", "--experimental-inspect", *QUIET],
                 ["run", "part.py", "--label", "second", *QUIET]):
        result = daemon.handle_request({"type": "run", "cwd": str(isolated_dir), "argv": args})
        payload = json.loads(result["output"])
        assert result["exit_code"] == 0 and payload["geometry_manifest"]
        assert "experimental_inspect" not in payload


def test_inspect_default_does_not_bypass_changes_gate(runner, isolated_dir):
    invoke(runner, ["init", "--name", "independent-gates"])
    (isolated_dir / "part.py").write_text("show_object(Box(10,20,5))\n")
    both = unpack(asyncio.run(server.mcp.call_tool("run", {
        "script": "part.py", "cwd": str(isolated_dir), "output": "both",
        "preview": False, "diff": False, "view": False, "idempotency_key": "both",
        "experimental_changes": True,
    })))
    assert both["geometry_manifest"] and both["change"] and both["status"] == "success"
    (isolated_dir / "part.py").unlink()
    status = {"idempotency_key": "both", "cwd": str(isolated_dir)}
    denied = unpack(asyncio.run(server.mcp.call_tool("run_status", status)))
    assert denied["reason"] == "experimental_changes_required"
    saved = unpack(asyncio.run(server.mcp.call_tool("run_status", {**status, "experimental_changes": True})))
    assert saved["status"] == "success" and saved["version"] == both["version"]
    assert saved["geometry_manifest"] == both["geometry_manifest"]


def test_old_ordinary_authored_run_replays_without_reindexing(runner, isolated_dir, monkeypatch):
    invoke(runner, ["init", "--name", "legacy-run"])
    (isolated_dir / "part.py").write_text("show_object(Box(20,30,6)-Cylinder(2,10))\n")
    args = ["run", "part.py", "--label", "legacy", "--idempotency-key", "old",
            "--experimental-changes", *QUIET]
    with monkeypatch.context() as patch:
        patch.setattr("agentcad.experimental_inspect.enabled", lambda: False)
        patch.setattr("agentcad.commands.run.inspect_enabled", lambda: False)
        old = invoke(runner, args)
    assert "geometry_manifest" not in old
    replay = invoke(runner, args)
    assert replay["version"] == old["version"] and replay["idempotency"]["replayed"]
    assert "geometry_manifest" not in replay
    (isolated_dir / "part.py").unlink()
    status = unpack(asyncio.run(server.mcp.call_tool("run_status", {
        "cwd": str(isolated_dir), "idempotency_key": "old", "experimental_changes": True,
    })))
    assert status["version"] == old["version"] and "geometry_manifest" not in status
