"""Real import/report/retry/snapshot delivery with complete response limits."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import shlex

import pytest
from mcp.types import CallToolResult

from agentcad.cli import cli
from agentcad.inspection_delivery import RESERVE, estimate
from agentcad.inspection_report import _estimated_tokens
from agentcad.mcp import server
from tests_b3d.test_inspection_report import _baseline

REAL = Path(__file__).parents[1] / "tests/fixtures/real_world/cadgenbench_101_agentcad.step"


def _payload(result):
    return result.structuredContent if isinstance(result, CallToolResult) else result


def _invoke(runner, args):
    result = runner.invoke(cli, [*args, "--no-daemon"])
    assert result.stdout, (result.exit_code, result.exception)
    return result, json.loads(result.stdout)


def _assert_budget(payload):
    audit = payload["delivery"]
    assert audit["budget_satisfied"], audit
    assert estimate(payload) == audit["estimated_text_tokens"]
    assert estimate(payload) + RESERVE <= audit["limits"]["text_tokens"]
    if audit["limits"]["combined_tokens"] is not None:
        assert audit["estimated_combined_tokens"] + RESERVE <= audit["limits"]["combined_tokens"]
    report = payload.get("inspection")
    if report:
        assert _estimated_tokens(report) == report["budget"]["estimated_text_tokens"]


@pytest.mark.parametrize("real", [False, True])
def test_complete_cli_mcp_report_budgets_and_saved_full_retrieval(runner, isolated_dir, monkeypatch, real):
    step, saved = _baseline(isolated_dir, REAL if real else None)
    args = ["inspect", str(step), "--report", "--response-text-budget", "3000",
            "--response-budget", "5500", "--image-budget", "2500", "--vision-profile", '{"name":"host","tokens_per_image":1000}']
    invoked, first = _invoke(runner, args)
    assert invoked.exit_code == 0, first
    _assert_budget(first)
    assert 0 < first["delivery"]["media"]["returned"] <= 2
    before = {p: p.read_bytes() for p in [step, step.with_name("geometry_manifest.json"), Path(first["response_snapshot"]["path"])]}
    before.update({p: p.read_bytes() for p in step.parent.joinpath("inspection").glob("*.*") if p.is_file()})
    mcp = asyncio.run(server.mcp.call_tool("inspect", {
        "file": str(step), "cwd": str(isolated_dir), "report": True, "response_text_budget": 3000,
        "response_budget": 5500, "image_budget": 2500, "vision_profile": {"name": "host", "tokens_per_image": 1000},
    }))
    assert isinstance(mcp, CallToolResult)
    actual = mcp.structuredContent
    _assert_budget(actual)
    assert len(mcp.content) - 1 == actual["delivery"]["media"]["returned"]
    assert [m["id"] for m in actual["inspection"]["media"]] == [m["id"] for m in first["inspection"]["media"]]
    assert actual["inspection"]["manifest_id"] == saved["manifest_id"]

    def unexpected(*a, **kw):
        pytest.fail("Saved response retrieval ran native analysis/rendering")

    monkeypatch.setattr("agentcad.inspection_report._analysis", unexpected)
    monkeypatch.setattr("agentcad.geometry_images.geometry_image", unexpected)
    retrieval = shlex.split(first["response_snapshot"]["retrieve"])
    recovered, full = _invoke(runner, retrieval[1:])
    assert recovered.exit_code == 0, full
    assert full["historical_response"] and full["snapshot_command"] == "inspect"
    assert full["manifest_id"] == first["inspection"]["manifest_id"]
    assert full["response_snapshot"]["id"] == first["response_snapshot"]["id"]
    assert len(full["media"]) >= len(first["inspection"]["media"])
    limited, smaller = _invoke(runner, [*retrieval[1:], "--image-budget", "0", "--response-text-budget", "3000"])
    assert limited.exit_code == 0
    _assert_budget(smaller)
    assert smaller["inspection"]["media"] == []
    assert {p: p.read_bytes() for p in before} == before


def test_import_retry_new_delivery_limits_preserve_acceptance_and_job(runner, isolated_dir, monkeypatch):
    monkeypatch.setattr("agentcad.project_viewer.handoff", lambda *a, **kw: None)
    build_name = "CAD — owner's files"
    (isolated_dir / "agentcad.toml").write_text("build_dir = " + json.dumps(build_name) + "\n")
    args = ["import", str(REAL), "--init", "--no-view", "--no-diff", "--idempotency-key", "delivery",
            "--response-text-budget", "5000", "--image-budget", "0"]
    first_result, first = _invoke(runner, args)
    assert first_result.exit_code == 0
    _assert_budget(first)
    assert first["promotion"]["outcome"] == "promoted"
    root = isolated_dir / build_name
    protected = {p: p.read_bytes() for p in [root / "agentcad.json", root / ".agentcad/changes/working.json",
                 Path(first["outputs"]["step"]), Path(first["geometry_manifest"]["path"]), isolated_dir / "edit.py"]}
    job_id = first["inspection_job"]["id"]
    monkeypatch.setattr("agentcad.commands.import_cmd._import_impl", lambda *a, **kw: pytest.fail("Budgeted retry reran import"))
    subdir = isolated_dir / "caller"
    subdir.mkdir()
    monkeypatch.chdir(subdir)
    args[args.index("--response-text-budget") + 1] = "4000"
    retry, second = _invoke(runner, args)
    assert retry.exit_code == 0
    _assert_budget(second)
    assert second["idempotency"]["replayed"]
    assert second["inspection_job"]["id"] == job_id
    for field in ("request_key", "outcome", "validation_state", "promoted_revision_before", "promoted_revision_after"):
        assert second["promotion"][field] == first["promotion"][field]
    assert {p: p.read_bytes() for p in protected} == protected
    retrieved, full = _invoke(runner, shlex.split(second["response_snapshot"]["retrieve"])[1:])
    assert retrieved.exit_code == 0, full
    assert full["historical_response"] and full["snapshot_command"] == "import"
    assert full["recorded_current_advanced"] is True and full["current_advanced"] is False
    from agentcad.change_promotion import PromotionStore
    assert full["promotion"] == PromotionStore(root).read(first["promotion"]["request_key"])["result"]
    assert {p: p.read_bytes() for p in protected} == protected
    assert len(list(root.glob("v*_*/output.step"))) == 1


def test_unknown_vision_profile_and_tiny_limit_keep_core_success(runner, isolated_dir, monkeypatch):
    monkeypatch.setattr("agentcad.project_viewer.handoff", lambda *a, **kw: None)
    imported, payload = _invoke(runner, ["import", str(REAL), "--init", "--no-view", "--no-diff",
        "--context-window", "20", "--max-images", "1"])
    assert imported.exit_code == 0
    assert payload["core"]["status"] == payload["status"] == "success"
    assert payload["delivery"]["status"] == "budget_unavailable"
    assert not payload["delivery"]["budget_satisfied"]
    assert payload["inspection"]["media"] == []
    assert Path(payload["response_snapshot"]["path"]).is_file()
    recovered, full = _invoke(runner, shlex.split(payload["response_snapshot"]["retrieve"])[1:])
    assert recovered.exit_code == 0 and full["promotion"]["outcome"] == "promoted"


def test_snapshot_missing_corrupt_baseline_and_project_bindings(runner, isolated_dir, monkeypatch):
    step, _ = _baseline(isolated_dir)
    first, payload = _invoke(runner, ["inspect", str(step), "--report", "--no-images", "--response-text-budget", "4000"])
    assert first.exit_code == 0
    action = shlex.split(payload["response_snapshot"]["retrieve"])[1:]
    path = Path(payload["response_snapshot"]["path"])
    original = path.read_bytes()
    path.write_text("{}")
    error, bad = _invoke(runner, action)
    assert error.exit_code == 1 and bad["reason"] == "response_snapshot_unavailable"
    path.write_bytes(original)
    wrong_baseline = [*action]
    wrong_baseline[1] = str(step.with_name("different.step"))
    error, mismatch = _invoke(runner, wrong_baseline)
    assert error.exit_code == 1 and "another baseline or project/build root" in mismatch["message"]
    other = isolated_dir / "other project"
    copied = other / ".agentcad/inspection_delivery" / path.name
    copied.parent.mkdir(parents=True)
    copied.write_bytes(original)  # A valid checksum is not another project's authority.
    monkeypatch.chdir(other)
    error, mismatch = _invoke(runner, ["inspect", str(step), "--report", "--response-snapshot", payload["response_snapshot"]["id"], "--build-dir", str(other)])
    assert error.exit_code == 1 and "another baseline or project/build root" in mismatch["message"]
    monkeypatch.chdir(isolated_dir)
    step.write_bytes(step.read_bytes() + b"\n")
    error, changed = _invoke(runner, action)
    assert error.exit_code == 1 and "Baseline changed" in changed["message"]
    path.unlink()
    error, missing = _invoke(runner, action)
    assert error.exit_code == 1 and missing["reason"] == "response_snapshot_unavailable"


@pytest.mark.parametrize("options", [
    ["--response-text-budget", "0"], ["--image-budget", "-1"],
    ["--vision-profile", '{"name":"host","tokens_per_image":true}'],
])
def test_invalid_caller_limits_do_not_import(runner, isolated_dir, monkeypatch, options):
    monkeypatch.setattr("agentcad.commands.import_cmd._import_impl", lambda *a, **kw: pytest.fail("Invalid budget executed import"))
    result = runner.invoke(cli, ["import", str(REAL), "--init", *options, "--no-daemon"])
    assert result.exit_code != 0
    assert not (isolated_dir / "agentcad.json").exists()


def test_mcp_attachment_failure_remains_bounded_and_successful(isolated_dir, monkeypatch):
    monkeypatch.setattr("agentcad.project_viewer.handoff", lambda *a, **kw: None)
    original = server._invoke

    def tamper(args, cwd=None):
        result = original(args, cwd=cwd)
        report = result["inspection"]
        png = Path(report["inventory"]["image_root"]) / report["media"][0]["png"]
        png.write_bytes(b"changed after CLI delivery")
        return result

    monkeypatch.setattr(server, "_invoke", tamper)
    result = server.import_cad(str(REAL), str(isolated_dir), initialize=True, view=False, diff=False,
                              max_images=1, response_text_budget=6000,
                              response_budget=8000, vision_profile={"name": "host", "tokens_per_image": 1000})
    assert isinstance(result, dict)
    assert result["status"] == result["core"]["status"] == "success" and result["_exit_code"] == 0
    assert result["image_delivery"]["status"] == "failed"
    assert result["delivery"]["media"]["returned"] == 0
    assert result["delivery"]["media"]["omission_reason"] == "image_delivery_failed"
    retry = shlex.split(result["image_delivery"]["next_actions"][0])
    assert retry[retry.index("--response-budget") + 1] == "8000"
    assert json.loads(retry[retry.index("--vision-profile") + 1])["tokens_per_image"] == 1000
    _assert_budget(result)


def test_snapshot_keeps_failed_import_exit_code(runner, isolated_dir):
    source = isolated_dir / "bad.step"
    source.write_text("not CAD")
    failed, response = _invoke(runner, ["import", str(source), "--init", "--no-view", "--response-text-budget", "3000"])
    assert failed.exit_code == 1 and response["status"] == "malformed"
    restored, historical = _invoke(runner, shlex.split(response["response_snapshot"]["retrieve"])[1:])
    assert restored.exit_code == 1
    assert historical["status"] == "malformed" and historical["historical_response"]
    assert historical["snapshot_command"] == "import"


def test_budgeted_job_wait_and_historical_pending_snapshot(runner, isolated_dir):
    step, _ = _baseline(isolated_dir, REAL)
    invoked, pending = _invoke(runner, ["inspect", str(step), "--report", "--no-images", "--wait-timeout", "0",
                                       "--response-text-budget", "5000"])
    assert invoked.exit_code == 0
    _assert_budget(pending)
    job_id = pending["inspection_job"]["id"]
    snapshot_id = pending["response_snapshot"]["id"]
    waited, complete = _invoke(runner, shlex.split(pending["inspection_job"]["actions"]["wait"])[1:])
    assert waited.exit_code == 0 and complete["inspection_job"]["status"] == "complete"
    historical, response = _invoke(runner, shlex.split(pending["response_snapshot"]["retrieve"])[1:])
    assert historical.exit_code == 0 and response["historical_response"]
    assert response["inspection_job"]["id"] == job_id
    assert response["inspection_job"]["status"] == pending["inspection_job"]["status"]
    assert response["response_snapshot"]["id"] == snapshot_id
    assert len(list(isolated_dir.joinpath("inspection/jobs").glob("*/request.json"))) == 1


@pytest.mark.parametrize("command,options", [
    ("import", ["--context-window", "128000", "--vision-profile", '{"name":"host","tokens_per_image":1000}']),
    ("inspect", ["--report", "--response-budget", "5000", "--response-text-budget", "2500", "--image-budget", "0"]),
])
def test_caller_budgets_reach_daemon_before_any_work(runner, isolated_dir, monkeypatch, command, options):
    import importlib
    calls = []

    def routed(argv, **kw):
        calls.extend(argv)
        raise SystemExit(0)

    module = importlib.import_module("agentcad.commands." + ("import_cmd" if command == "import" else "inspect_cmd"))
    monkeypatch.setattr(module, "maybe_route_through_daemon", routed)
    invoked = runner.invoke(cli, [command, "missing.step", *options])
    assert invoked.exit_code == 0
    for i, value in enumerate(options):
        if value.startswith("--"):
            assert value in calls
            if value != "--report":
                forwarded = calls[calls.index(value) + 1]
                assert json.loads(forwarded) == json.loads(options[i + 1]) if value == "--vision-profile" else forwarded == options[i + 1]
    assert not list(isolated_dir.rglob("request.json"))


def test_registered_inspect_rejects_unknown_budget_fields_before_execution(isolated_dir, monkeypatch):
    monkeypatch.setattr(server, "_invoke", lambda *a, **kw: pytest.fail("Unknown budget option executed inspection"))
    with pytest.raises(ValueError, match="Invalid inspect arguments"):
        asyncio.run(server.mcp.call_tool("inspect", {"file": "missing.step", "cwd": str(isolated_dir),
                                                     "report": True, "response_buget": 1000}))
