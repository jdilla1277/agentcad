"""Local MCP import delivers CLI evidence and actual version-linked images."""

import asyncio
import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shlex

import pytest
from PIL import Image
from mcp.types import CallToolResult

from agentcad.geometry_manifest import load_geometry, read_manifest
from agentcad.mcp import server
from tests_b3d.test_inspection_report import _distinct_parts
from tests_b3d.test_step_assembly import _nested_step


def _source(root, kind="nested"):
    if kind == "cadgenbench":
        return Path(__file__).parents[1] / "tests/fixtures/real_world/cadgenbench_101_agentcad.step"
    if kind == "brep":
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.BRepTools import BRepTools

        path = root / "supplier body.brep"
        assert BRepTools.Write_s(BRepPrimAPI_MakeBox(10, 20, 5).Shape(), str(path))
        return path
    return _nested_step(root / "named supplier.step", tilted=True)


def _payload(result):
    return result.structuredContent if isinstance(result, CallToolResult) else result


def _verify_images(result, cwd):
    assert isinstance(result, CallToolResult)
    payload = result.structuredContent
    assert json.loads(result.content[0].text) == payload
    media = payload["inspection"]["media"]
    assert [block.type for block in result.content] == ["text", *["image"] * len(media)]
    root = Path(payload["inspection"]["inventory"]["image_root"])
    if not root.is_absolute():
        root = cwd / root
    for block, entry in zip(result.content[1:], media):
        png = base64.b64decode(block.data)
        assert block.mimeType == "image/png"
        assert hashlib.sha256(png).hexdigest() == entry["sha256"]
        assert png == (root / entry["png"]).read_bytes()
        with Image.open(io.BytesIO(png)) as image:
            assert image.size == (1024, 1024)
    return payload


@pytest.mark.parametrize("initialize", [False, True])
@pytest.mark.parametrize("deferred", [False, True])
def test_registered_import_applies_declared_checks_without_advancing_current(
    isolated_dir, initialize, deferred
):
    from agentcad.change_checks import measure_geometry

    source = _source(isolated_dir, "cadgenbench")
    project = isolated_dir / "checked project"
    project.mkdir()
    build = project / "artifacts"
    baseline = None
    if not initialize:
        baseline = _payload(server.import_cad(
            str(source), str(project), label="accepted", initialize=True,
            view=False, diff=False, report=False, report_images=False, build_dir="artifacts",
        ))
        assert baseline["status"] == "success", baseline
        accepted_step = Path(baseline["outputs"]["step"])
        accepted_bytes = accepted_step.read_bytes()
    expected = measure_geometry(source)["dimensions"]["x"] + (0 if deferred else 10)
    checks = isolated_dir / "requirements.json"
    checks.write_text(json.dumps({"schema_version": 1, "checks": [{
        "check_id": "length", "kind": "dimension", "axis": "x",
        "expected_mm": expected, "tolerance_mm": 0.1,
    }]}))
    result = asyncio.run(server.mcp.call_tool("import_cad", {
        "file": str(source), "cwd": str(project), "label": "checked-import",
        "initialize": initialize, "checks_file": str(checks), "defer_checks": deferred,
        "view": False, "diff": False, "report": False, "report_images": False,
        "build_dir": "artifacts",
    }))
    # The registered tool transports a refused import as one JSON text block.
    text, = result
    assert text.type == "text"
    payload = json.loads(text.text)
    assert payload["status"] == ("checks_pending" if deferred else "checks_failed")
    assert payload["_exit_code"] == 1
    assert payload["outputs"]["step"] is None
    assert payload["current_advanced"] is False
    assert Path(payload["change"]["geometry"]).is_relative_to(build)
    receipt = payload["change"]["validation_checks"]
    assert receipt["validation_state"] == ("pending" if deferred else "invalid")
    row = next(r for r in receipt["checks"] if r["check_id"] == "length")
    assert row["state"] == ("pending" if deferred else "fail")
    manifest = json.loads((build / "agentcad.json").read_text())
    assert manifest.get("current") == (None if initialize else "accepted")
    assert len(manifest["versions"]) == (0 if initialize else 1)
    if baseline:
        assert accepted_step.read_bytes() == accepted_bytes


@pytest.mark.parametrize("kind", ["cadgenbench", "nested", "brep"])
def test_registered_import_delivers_cli_json_and_checked_pngs(isolated_dir, monkeypatch, kind):
    source = _source(isolated_dir, kind)
    project = isolated_dir / "project with spaces"
    project.mkdir()
    caller = isolated_dir / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    original = server._invoke
    captured = []

    def observed(args, cwd=None):
        result = original(args, cwd=cwd)
        captured.append(deepcopy(result))  # Real CLI JSON before image transport.
        return result

    monkeypatch.setattr(server, "_invoke", observed)
    result = asyncio.run(server.mcp.call_tool("import_cad", {
        "file": str(source), "cwd": str(project), "initialize": True,
        "view": False, "diff": False, "build_dir": "build with spaces",
    }))
    payload = _verify_images(result, project)
    assert payload == captured[0]
    assert payload["status"] == "success" and payload["_exit_code"] == 0
    assert len(result.content) == 6  # One text + overall + four raw-feature samples.
    assert Path.cwd() == caller
    step = Path(payload["outputs"]["step"])
    assert step.is_absolute() and "build with spaces" in str(step)
    saved = read_manifest(step)
    assert payload["geometry_manifest"]["manifest_id"] == saved["manifest_id"]
    assert payload["inspection"]["quality"]["is_valid"] is True
    assert payload["inspection"]["recognition"]["recognized_feature_count"] is None
    assert saved["source"]["filename"] == source.name
    assert (project / "edit.py").is_file()
    assert payload["change"]["status"] == "completed"
    context = load_geometry(step)
    image_root = Path(payload["inspection"]["inventory"]["image_root"])
    for entry in payload["inspection"]["media"]:
        sheet = json.loads((image_root / entry["png"]).with_suffix(".json").read_text())
        assert all(label["id"] in saved["entities"] for label in sheet["annotations"])
        if entry["id"] != "overall":
            assert sheet["request"]["object_id"] == entry["id"]
            assert context.record(entry["id"])["kind"] in {"part", "face", "edge"}


def test_import_delivers_at_most_nine_ranked_images(isolated_dir):
    source = _distinct_parts(isolated_dir / "assembly.step")
    result = server.import_cad(str(source), str(isolated_dir), initialize=True, view=False, diff=False)
    payload = _verify_images(result, isolated_dir)
    assert len(result.content[1:]) == 9
    assert payload["inspection"]["inventory"]["parts_total"] == 12
    assert [entry["role"] for entry in payload["inspection"]["media"]] == [
        "overall", *[role for _ in range(4) for role in ("parts", "features")],
    ]


@pytest.mark.parametrize(("options", "count"), [
    ({"report": False}, 0), ({"report_images": False}, 0),
    ({"max_images": 0}, 0), ({"max_images": 2}, 2),
])
def test_import_report_and_image_controls_preserve_saved_inventory(isolated_dir, options, count):
    source = _source(isolated_dir)
    result = server.import_cad(str(source), str(isolated_dir), initialize=True, view=False, diff=False, **options)
    payload = _payload(result)
    assert payload["_exit_code"] == 0
    assert isinstance(result, CallToolResult) == bool(count)
    if count:
        _verify_images(result, isolated_dir)
        assert len(result.content[1:]) == count
    assert ("inspection" in payload) == options.get("report", True)
    step = Path(payload["outputs"]["step"])
    assert step.is_file()
    assert Path(payload["outputs"]["inspection_report"]).is_file()
    assert Path(payload["outputs"]["inspection_bundle"]).is_file()
    registry = step.with_name("geometry_manifest.json").read_bytes()
    if options.get("report", True):
        assert len(payload["inspection"]["media"]) == count
        replay = server.inspect(file=str(step), cwd=str(isolated_dir), report=True, report_images=False)
        assert replay["analysis_revision"] == payload["inspection"]["analysis_revision"]
    else:
        catalogue = json.loads(Path(payload["outputs"]["inspection_bundle"]).read_text())
        assert len(catalogue["media"]) == 5  # Report-off still prepares the bundle.
    assert step.with_name("geometry_manifest.json").read_bytes() == registry


@pytest.mark.parametrize("fault", ["missing", "hash", "not_png"])
def test_attachment_failure_preserves_core_and_supplies_retry(isolated_dir, monkeypatch, fault):
    source = _source(isolated_dir)
    original = server._invoke

    def tampered(args, cwd=None):
        payload = original(args, cwd=cwd)
        report = payload["inspection"]
        medium = report["media"][0]
        path = Path(report["inventory"]["image_root"]) / medium["png"]
        if fault == "missing":
            path.unlink()
        else:
            path.write_bytes(b"not a PNG")
            if fault == "not_png":
                medium["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return payload

    monkeypatch.setattr(server, "_invoke", tampered)
    payload = server.import_cad(str(source), str(isolated_dir), initialize=True, view=False, diff=False, max_images=1)
    assert isinstance(payload, dict)  # Delivery is atomic: no unchecked image blocks.
    assert payload["status"] == payload["core"]["status"] == "success"
    assert payload["_exit_code"] == 0
    assert payload["image_delivery"]["status"] == "failed"
    assert payload["image_delivery"]["attached"] == 0
    assert payload["image_delivery"]["requested"] == 1
    step = Path(payload["outputs"]["step"])
    context = load_geometry(step)
    assert context.manifest_id == payload["geometry_manifest"]["manifest_id"]
    registry = step.with_name("geometry_manifest.json").read_bytes()
    monkeypatch.setattr(server, "_invoke", original)
    retry = shlex.split(payload["image_delivery"]["next_actions"][0])
    cli_retried = original(retry[1:], cwd=str(isolated_dir))
    assert cli_retried["status"] == "success"
    assert cli_retried["budget"]["max_images"] == 1
    assert cli_retried["budget"]["text_tokens"] == payload["inspection"]["budget"]["text_tokens"]
    assert len(cli_retried["media"]) == 1
    retried = server.inspect(file=str(step), cwd=str(isolated_dir), report=True, max_images=1)
    assert isinstance(retried, CallToolResult) and len(retried.content) == 2
    assert step.with_name("geometry_manifest.json").read_bytes() == registry
    history = json.loads((isolated_dir / "agentcad.json").read_text())
    assert len(history["versions"]) == 1


def test_render_failure_remains_partial_import_success(isolated_dir, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("graphics unavailable")

    monkeypatch.setattr("agentcad.render.render_geometry_sheet", broken)
    source = _source(isolated_dir)
    result = server.import_cad(str(source), str(isolated_dir), initialize=True, view=False, diff=False)
    assert isinstance(result, dict)
    assert result["status"] == "success" and result["_exit_code"] == 0
    assert result["inspection"]["media_summary"]["state"] == "partial_failure"
    assert result["inspection"]["media_summary"]["failed"] == 5
    assert Path(result["outputs"]["step"]).is_file()
    assert (isolated_dir / "edit.py").is_file()


@pytest.mark.parametrize("error", ["uninitialized", "malformed", "invalid", "runtime", "budget"])
def test_import_keeps_cli_errors_and_diagnostics(isolated_dir, error):
    source = _source(isolated_dir)
    options = {"initialize": True}
    if error == "uninitialized":
        options["initialize"] = False
    elif error == "malformed":
        source = isolated_dir / "bad.step"
        source.write_text("not a STEP file", encoding="utf-8")
    elif error == "invalid":
        source = Path(__file__).parents[1] / "tests/fixtures/validation/open_shell.step"
    elif error == "runtime":
        assert server._invoke(["init"], cwd=str(isolated_dir))["_exit_code"] == 0
        options["runtime"] = "cadquery"  # Existing-project guard needs no CadQuery install.
    else:
        options["text_budget"] = 0
    result = server.import_cad(str(source), str(isolated_dir), view=False, diff=False, **options)
    assert isinstance(result, dict)
    assert result["_exit_code"] != 0
    assert "inspection" not in result
    assert not (isolated_dir / "edit.py").exists()
    if error == "invalid":
        assert result["status"] == "invalid_geometry"
        assert result["change"]["revision_id"]
        assert Path(result["outputs"]["source"]).is_file()
    elif error == "malformed":
        assert result["status"] == "malformed"
        assert result["change"]["revision_id"]
        assert json.loads((isolated_dir / "agentcad.json").read_text())["versions"] == []
    elif error in {"budget", "uninitialized"}:
        assert not (isolated_dir / "agentcad.json").exists()
    else:
        assert "--runtime only applies" in result["message"]
