"""Snapshot eviction preserves real imports, issued IDs and resumable job evidence."""

import asyncio
import hashlib
import json
from pathlib import Path
import shlex

import pytest

from agentcad.mcp import server
from tests_b3d.test_inspection_budgets import REAL, _assert_budget, _invoke, _payload
from tests_b3d.test_inspection_report import _baseline


def _protected(root):
    files = [path for path in root.rglob("*") if path.is_file() and (
        path.suffix in {".step", ".brep", ".py"} or path.name in {"agentcad.json", "geometry_manifest.json", "working.json"}
        or "/inspection/jobs/" in path.as_posix() or "/import_requests/" in path.as_posix())]
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files if path.suffix != ".lock"}


def _assert_unchanged(before):
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before} == before


@pytest.mark.parametrize("real", [False, True])
def test_report_snapshot_expiry_cli_mcp_and_fresh_recovery(runner, isolated_dir, monkeypatch, real):
    step, manifest = _baseline(isolated_dir, REAL if real else None)
    (isolated_dir / "agentcad.toml").write_text('[inspection.snapshots]\nmax_age_seconds = 10\nmax_bytes = 1048576\n')
    monkeypatch.setattr("agentcad.inspection_snapshots._now", lambda: 1000)
    result, response = _invoke(runner, ["inspect", str(step), "--report", "--response-text-budget", "5000", "--max-images", "1"])
    assert result.exit_code == 0, response
    _assert_budget(response)
    ref = response["response_snapshot"]
    assert ref["retention"]["max_age_seconds"] == 10 and ref["retention"]["may_evict_earlier"]
    before = _protected(isolated_dir)
    snapshot_bytes = Path(ref["path"]).read_bytes()
    child = isolated_dir / "caller"
    child.mkdir()
    monkeypatch.chdir(child)
    restored, full = _invoke(runner, shlex.split(ref["retrieve"])[1:])
    assert restored.exit_code == 0 and full["manifest_id"] == manifest["manifest_id"]
    assert full["response_snapshot"]["retention"] == ref["retention"]
    assert Path(ref["path"]).read_bytes() == snapshot_bytes
    monkeypatch.setattr("agentcad.inspection_snapshots._now", lambda: 1010)
    with monkeypatch.context() as guard:
        guard.setattr("agentcad.inspection_report._analysis", lambda *a, **kw: pytest.fail("Expired snapshot ran CAD analysis"))
        guard.setattr("agentcad.geometry_images.geometry_image", lambda *a, **kw: pytest.fail("Expired snapshot rendered new evidence"))
        expired, error = _invoke(runner, shlex.split(ref["retrieve"])[1:])
        assert expired.exit_code == 1 and error["reason"] == "response_snapshot_expired"
        assert "fresh" in error["suggestion"] and not Path(ref["path"]).exists()
        mcp = _payload(asyncio.run(server.mcp.call_tool("inspect", {**({
            "file": str(step), "cwd": str(child), "report": True,
            "response_snapshot": ref["id"], "build_dir": str(isolated_dir),
            "response_text_budget": 5000, "image_budget": 0,
        }), "experimental_inspect": True})))
        assert mcp["_exit_code"] == 1 and mcp["reason"] == "response_snapshot_unavailable"
        assert mcp["next_actions"] == error["next_actions"]
    fresh, current = _invoke(runner, shlex.split(error["next_actions"][0])[1:])
    assert fresh.exit_code == 0 and current["manifest_id"] == manifest["manifest_id"]
    assert not current.get("historical_response")
    _assert_unchanged(before)


def test_import_quota_eviction_and_oversized_retry_keep_accepted_geometry(runner, isolated_dir, monkeypatch):
    monkeypatch.setattr("agentcad.project_viewer.handoff", lambda *a, **kw: None)
    monkeypatch.setattr("agentcad.inspection_snapshots._now", lambda: 1000)
    build_name = "CAD — owner's files"
    config = isolated_dir / "agentcad.toml"
    config.write_text("build_dir = " + json.dumps(build_name) + "\n[inspection.snapshots]\nmax_bytes = 1048576\n")
    args = ["import", str(REAL), "--init", "--no-view", "--no-diff", "--idempotency-key", "retention",
            "--response-text-budget", "6000", "--max-images", "1"]
    result, first = _invoke(runner, args)
    assert result.exit_code == 0 and first["promotion"]["outcome"] == "promoted"
    _assert_budget(first)
    root = isolated_dir / build_name
    before = _protected(isolated_dir)
    snapshot_path = Path(first["response_snapshot"]["path"])
    quota = snapshot_path.stat().st_size + 256
    config.write_text("build_dir = " + json.dumps(build_name) + f"\n[inspection.snapshots]\nmax_bytes = {quota}\n")
    monkeypatch.setattr("agentcad.inspection_snapshots._now", lambda: 1001)
    monkeypatch.setattr("agentcad.commands.import_cmd._import_impl", lambda *a, **kw: pytest.fail("Retention change repeated import"))
    retry, second = _invoke(runner, args)
    assert retry.exit_code == 0 and second["idempotency"]["replayed"]
    assert second["inspection_job"]["id"] == first["inspection_job"]["id"]
    assert second["change"]["revision_id"] == first["change"]["revision_id"]
    assert not snapshot_path.exists() and Path(second["response_snapshot"]["path"]).exists()
    assert sum(p.stat().st_size for p in root.joinpath(".agentcad/inspection_delivery").glob("delivery_*.json")) <= quota
    missing, error = _invoke(runner, shlex.split(first["response_snapshot"]["retrieve"])[1:])
    assert missing.exit_code == 1 and error["reason"] == "response_snapshot_unavailable"
    _assert_unchanged(before)
    kept = Path(second["response_snapshot"]["path"]).read_bytes()
    config.write_text("build_dir = " + json.dumps(build_name) + "\n[inspection.snapshots]\nmax_bytes = 100\n")
    small, response = _invoke(runner, args)
    assert small.exit_code == 0 and response["core"]["status"] == "success"
    assert response["delivery"]["status"] == "resource_limit" and not response["delivery"]["budget_satisfied"]
    assert response["delivery"]["media"]["returned"] == 0 and "max_bytes" in response["delivery"]["message"]
    assert Path(second["response_snapshot"]["path"]).read_bytes() == kept
    mcp = asyncio.run(server.mcp.call_tool("import_cad", {**({
        "file": str(REAL), "cwd": str(isolated_dir), "initialize": True, "view": False, "diff": False,
        "idempotency_key": "retention", "response_text_budget": 6000, "max_images": 1,
    }), "experimental_inspect": True}))
    payload = _payload(mcp)
    assert payload["_exit_code"] == 0 and payload["delivery"]["status"] == "resource_limit"
    assert all(block.type == "text" for block in mcp.content)
    _assert_unchanged(before)
    assert len(list(root.glob("v*_*/output.step"))) == 1


def test_compact_projection_retains_expiry_and_explains_unknown_context(runner, isolated_dir):
    step, _ = _baseline(isolated_dir)
    result, response = _invoke(runner, ["inspect", str(step), "--report", "--no-images", "--response-text-budget", "1"])
    assert result.exit_code == 0 and not response["delivery"]["budget_satisfied"]
    assert response["response_snapshot"]["retention"]["state"] == "retained"
    assert response["response_snapshot"]["retention"]["expires_at"]
    assert response["response_snapshot"]["retention"]["may_evict_earlier"] is True
