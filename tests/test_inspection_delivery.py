"""Whole JSON/image budgets are a delivery projection, not geometry changes."""
from copy import deepcopy
import json

import pytest

from agentcad.inspection_delivery import RESERVE, constrain, estimate, finalize_mcp, policy_values


@pytest.fixture
def response():
    media = [{"id": "overall" if i == 0 else f"face_v1_{i}", "png": f"image_{i}.png", "sha256": "a" * 64} for i in range(9)]
    return {
        "command": "import", "status": "success", "core": {"status": "success"},
        "current_advanced": True, "promotion": {"outcome": "promoted", "request_key": "original", "validation_state": "valid"},
        "outputs": {"step": "/project/v1/output.step", "glb": "/project/v1/output.glb"},
        "validation": {"is_valid": True, "layers": {"details": "very long detail " * 1000}},
        "change": {"revision_id": "r_original", "status": "completed", "receipt": "/project/receipt.json"},
        "inspection": {
            "command": "inspect", "status": "success", "manifest_id": "original", "analysis_revision": "original-analysis",
            "counts": {"face": 9}, "recognition": {"status": "not_available", "recognized_feature_count": None},
            "model": {"dimensions": {"x": 10, "y": 20, "z": 30}},
            "inventory": {"manifest": "/project/geometry_manifest.json", "report": "/project/report.json", "bundle": "/project/bundle.json", "image_root": "/project/images"},
            "parts": [{"id": "part_v1_1", "name": "supplier part", "dimensions": {"x": 10}}],
            "samples": {"parts": [], "features": [{"id": f"face_v1_{i}", "dimensions": {"x": i}} for i in range(1, 9)]},
            "media": media,
            "media_summary": {"prepared": 9, "returned": 9, "omitted": 0, "failed": 0, "state": "complete"},
            "next_actions": ["agentcad inspect /project/output.step --report"],
            "budget": {"scope": "inspection_report", "text_tokens": 10000, "max_images": 9, "transport_reserve_tokens": 10, "truncated": False},
        },
    }


REF = {"id": "delivery_saved", "path": "/project/full.json", "retrieve": "agentcad inspect /project/output.step --report --response-snapshot delivery_saved"}


def _assert_budget(payload):
    budget = payload["delivery"]
    assert estimate(payload) == budget["estimated_text_tokens"]
    if budget["budget_satisfied"]:
        assert estimate(payload) + RESERVE <= budget["limits"]["text_tokens"]
        if budget["limits"]["image_tokens"] is not None:
            assert budget["estimated_image_tokens"] <= budget["limits"]["image_tokens"]
        if budget["limits"]["combined_tokens"] is not None:
            assert budget["estimated_combined_tokens"] == estimate(payload) + budget["estimated_image_tokens"]
            assert budget["estimated_combined_tokens"] + RESERVE <= budget["limits"]["combined_tokens"]


@pytest.mark.parametrize("kwargs", [
    {"context_window": 128000, "vision_profile": {"name": "host", "tokens_per_image": 1000}},
    {"response_budget": 5000, "image_budget": 1500, "vision_profile": {"name": "host", "tokens_per_image": 1000}},
    {"response_text_budget": 2500},
    {"image_budget": 0},
    {"context_window": 100000},
    {"response_budget": 1200, "vision_profile": {"name": "host", "tokens_per_image": 500}},
])
def test_limits_preserve_identity_and_priority(response, kwargs):
    before = deepcopy(response)
    delivered = constrain(response, policy_values(**kwargs), REF)
    _assert_budget(delivered)
    assert response == before
    assert delivered["core"] == before["core"]
    assert delivered["current_advanced"] is True
    assert delivered["promotion"] == before["promotion"]
    assert delivered["change"]["revision_id"] == "r_original"
    report = delivered["inspection"]
    ids = [row["id"] for row in report["media"]]
    original_ids = [row["id"] for row in response["inspection"]["media"]]
    assert ids == original_ids[:len(ids)]
    assert delivered["delivery"]["media"]["omitted_ids"] == original_ids[len(ids):]
    assert report["media_summary"]["returned"] == len(ids)
    assert report["media_summary"]["omitted"] == 9 - len(ids)
    assert report["recognition"]["recognized_feature_count"] is None


def test_context_override_and_unknown_vision_cost(response):
    explicit = constrain(response, policy_values(context_window=10000, response_budget=7000,
                            vision_profile={"name": "host", "tokens_per_image": 1000}), REF)
    assert explicit["delivery"]["limits"]["combined_tokens"] == 7000
    assert explicit["delivery"]["target_source"] == "explicit_response_budget"
    unknown = constrain(response, policy_values(context_window=100000), REF)
    assert unknown["inspection"]["media"] == []
    assert unknown["delivery"]["media"]["omission_reason"] == "vision_profile_required"
    assert unknown["delivery"]["estimated_image_tokens"] == 0
    assert unknown["delivery"]["vision_estimation"] == "unknown"
    _assert_budget(unknown)


def test_too_small_is_explicit_and_core_success_survives(response):
    result = constrain(response, policy_values(response_budget=1), REF)
    assert result["delivery"]["budget_satisfied"] is False
    assert result["delivery"]["status"] == "budget_unavailable"
    assert result["core"]["status"] == result["status"] == "success"
    assert result["response_snapshot"] == REF
    assert result["inspection"]["media"] == []
    _assert_budget(result)


def test_mcp_exit_code_and_failed_attachments_are_rebudgeted(response):
    first = constrain(response, policy_values(response_budget=6000, vision_profile={"name": "host", "tokens_per_image": 1000}), REF)
    original_omitted = first["delivery"]["media"]["omitted_ids"]
    second = finalize_mcp({**first, "_exit_code": 0})
    _assert_budget(second)
    assert all(i in second["delivery"]["media"]["omitted_ids"] for i in original_omitted)
    failed = finalize_mcp({**second, "image_delivery": {"status": "failed", "message": "changed image"}}, delivered=False)
    _assert_budget(failed)
    assert failed["delivery"]["media"]["returned"] == 0
    assert failed["delivery"]["media"]["omission_reason"] == "image_delivery_failed"
    assert len(failed["delivery"]["media"]["omitted_ids"]) == 9


@pytest.mark.parametrize("bad", [
    {"context_window": 0}, {"response_budget": -1}, {"image_budget": -1}, {"response_text_budget": True},
    {"vision_profile": "not json"}, {"vision_profile": {"name": "host", "tokens_per_image": 0}},
    {"vision_profile": {"name": "host", "tokens_per_image": 10, "extra": 1}},
])
def test_invalid_policy_is_rejected(bad):
    with pytest.raises(ValueError):
        policy_values(**bad)


@pytest.mark.parametrize("failure", ["disk", "layout"])
def test_snapshot_storage_failure_preserves_result_and_suppresses_images(response, tmp_path, monkeypatch, failure):
    from types import SimpleNamespace
    from agentcad.inspection_delivery import apply_delivery
    from agentcad.project import ProjectLayout, ProjectError

    step = tmp_path / "output.step"
    step.write_bytes(b"retained baseline")
    response["outputs"]["step"] = str(step)
    original = deepcopy(response)
    ctx = SimpleNamespace(meta={"inspection_delivery_policy": policy_values(image_budget=0),
                              "inspection_delivery_source": str(step), "inspection_delivery_argument": str(step)})

    def failed(*a, **kw):
        if failure == "layout":
            raise ProjectError("artifact_path_escape", "snapshot directory points outside the build root", "Restore the cache directory.")
        raise OSError("disk full")

    monkeypatch.setattr("agentcad.inspection_delivery.atomic_write_json", failed)
    result = apply_delivery(response, ProjectLayout(tmp_path, tmp_path), ctx)
    assert result["status"] == result["core"]["status"] == "success"
    assert result["promotion"] == original["promotion"]
    assert result["delivery"]["status"] == "snapshot_unavailable"
    assert not result["delivery"]["budget_satisfied"]
    assert result["delivery"]["media"]["returned"] == 0
    assert response == original
    mcp = finalize_mcp({**result, "_exit_code": 0})
    assert estimate(mcp) == mcp["delivery"]["estimated_text_tokens"]


def test_daemon_projection_preserves_original_full_snapshot(response, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agentcad.inspection_delivery import apply_delivery
    from agentcad.project import ProjectLayout

    requested = policy_values(response_budget=5000, vision_profile={"name": "host", "tokens_per_image": 1000})
    first = constrain(response, requested, REF)
    monkeypatch.setattr("agentcad.inspection_delivery.save_snapshot", lambda *a: pytest.fail("Transport snapshotted an already truncated response"))
    result = apply_delivery({**first, "via": "daemon"}, ProjectLayout(tmp_path, tmp_path),
                            SimpleNamespace(meta={"inspection_delivery_policy": requested}))
    _assert_budget(result)
    assert result["response_snapshot"] == REF
    assert result["delivery"]["media"]["eligible"] == 9


def test_no_policy_does_not_resolve_or_reinterpret_legacy_file_arguments():
    import click
    from click.testing import CliRunner
    from agentcad.inspection_delivery import delivery_options

    received = []

    @click.command()
    @click.argument("file")
    @delivery_options
    def command(file):
        received.append(file)

    result = CliRunner().invoke(command, ["invalid\0.step"])
    assert result.exit_code == 0
    assert received == ["invalid\0.step"]
