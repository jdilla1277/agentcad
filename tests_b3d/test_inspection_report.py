"""Bounded default reports preserve inventories, identities and actual media."""

import asyncio
import base64
import hashlib
import json
import math
from pathlib import Path

import pytest
from PIL import Image

from agentcad.cli import cli
from agentcad.geometry_manifest import create_manifest, read_manifest
from agentcad.inspection_report import inspection_report, ReportBudgetError
from agentcad.inspection_samples import select_samples
from agentcad.step_assembly import read_step_assembly, write_step_assembly
from tests_b3d.test_step_assembly import _nested_step


def _baseline(root, source=None):
    source = source or _nested_step(root / "source.step", tilted=True)
    step = root / "output.step"
    write_step_assembly(read_step_assembly(source), step)
    return step, create_manifest(step, 1, source={"filename": source.name, "assembly_metadata": "preserved_source_document"})


def _distinct_parts(path, count=12):
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopoDS import TopoDS_Compound
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.IFSelect import IFSelect_RetDone
    from agentcad.native_io import silence_native_stdout

    document = TDocStd_Document(TCollection_ExtendedString("XCAF"))
    tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    compound = TopoDS_Compound()
    BRep_Builder().MakeCompound(compound)
    parent = tool.AddShape(compound, True)
    for i in range(count):
        label = tool.AddShape(BRepPrimAPI_MakeBox(i + 1, i * 2 + 3, 5).Shape(), False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(f"Supplier part {i}"))
        transform = gp_Trsf()
        transform.SetTranslation(gp_Vec(i * 30, 0, 0))
        tool.AddComponent(parent, label, TopLoc_Location(transform))
    tool.UpdateAssemblies()
    with silence_native_stdout():
        writer = STEPCAFControl_Writer()
        assert writer.Transfer(document, STEPControl_AsIs)
        assert writer.Write(str(path)) == IFSelect_RetDone
    return path


def _json(runner, args):
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.mark.parametrize("name", ["cadgenbench_101_agentcad.step", "pump_manifold.step"])
def test_real_bundle_reuses_labeled_overall_and_preserves_identity(isolated_dir, name):
    fixture = Path(__file__).parents[1] / "tests" / "fixtures" / "real_world" / name
    step, saved = _baseline(isolated_dir, fixture)
    registry = step.with_name("geometry_manifest.json").read_bytes()
    result, prepared = inspection_report(step, isolated_dir, text_budget=5000)
    assert result["recognition"] == {"status": "not_available", "recognized_feature_count": None}
    assert result["baseline_sha256"] == saved["baseline"]["sha256"]
    assert result["quality"]["status"] == "not_available"  # No import validation metadata here.
    assert len(result["samples"]["parts"]) == 1
    assert len(result["samples"]["features"]) == 4
    assert {row["kind"] for row in result["samples"]["features"]} == {"face", "edge"}
    assert len(result["media"]) == 5
    part = result["samples"]["parts"][0]
    assert part["image_id"] == "overall"
    assert result["media_summary"]["reused_overall_for"] == part["id"]
    assert [row["id"] for row in result["media"]] == ["overall", *[r["id"] for r in result["samples"]["features"]]]
    for medium in result["media"]:
        path = Path(result["inventory"]["image_root"]) / medium["png"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == medium["sha256"]
        with Image.open(path) as image:
            assert image.size == (1024, 1024)
    overall_path = Path(result["inventory"]["image_root"]) / result["media"][0]["png"]
    overall = json.loads(overall_path.with_suffix(".json").read_text())
    assert part["id"] in {row["id"] for row in overall["annotations"]}
    assert step.with_name("geometry_manifest.json").read_bytes() == registry
    assert read_manifest(step)["manifest_id"] == saved["manifest_id"]
    without, other = inspection_report(step, isolated_dir, images=False, text_budget=5000)
    assert without["analysis_revision"] == result["analysis_revision"]
    assert without["samples"] == result["samples"]
    assert without["counts"] == result["counts"]
    assert prepared["report"] == other["report"]
    assert without["media"] == []


def test_ranking_and_full_parts_inventory_are_deterministic_and_budgeted(isolated_dir):
    step, saved = _baseline(isolated_dir, _distinct_parts(isolated_dir / "supplier.step"))
    plan = select_samples(saved)
    assert plan == select_samples(saved)
    assert len(plan["parts"]) == len(plan["features"]) == 4
    expected_biggest = max((e for e in saved["entities"].values() if e["kind"] == "part" and e["definition_kind"] == "part"), key=lambda e: e["volume"])
    assert plan["parts"][0]["id"] == expected_biggest["id"]
    result, prepared = inspection_report(step, isolated_dir, images=False, text_budget=2000)
    assert result["inventory"]["parts_total"] == 12
    assert result["budget"]["truncated"]
    assert result["budget"]["parts_returned"] == len(result["parts"]) <= 10
    assert len(prepared["analysis"]["parts"]) == 12
    assert math.ceil(len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) / 3) <= 2000
    capped, _ = inspection_report(step, isolated_dir, max_images=3, text_budget=5000)
    assert len(capped["media"]) == 3
    assert [r["role"] for r in capped["media"]] == ["overall", "parts", "features"]
    assert capped["media_summary"]["omitted"] == 6
    assert capped["media_summary"]["state"] == "bounded"
    full, _ = inspection_report(step, isolated_dir, max_images=9, text_budget=8000)
    assert len(full["media"]) == 9
    assert [row["role"] for row in full["media"]] == ["overall", *[role for _ in range(4) for role in ("parts", "features")]]
    default, _ = inspection_report(step, isolated_dir)
    assert default["parts"]  # Names survive fallback budgeting ahead of optional detail.
    assert len(default["media"]) == 9
    assert default["budget"]["estimated_text_tokens"] <= 2000
    with pytest.raises(ReportBudgetError, match="budget is too small"):
        inspection_report(step, isolated_dir, images=False, text_budget=1)


def test_context_and_cached_analysis_are_reused_and_corruption_recovers(isolated_dir, monkeypatch):
    import agentcad.inspection_report as reports
    import agentcad.geometry_images as images

    step, _ = _baseline(isolated_dir)
    original = reports.BaselineGeometry
    calls = []

    def counted(path):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(reports, "BaselineGeometry", counted)
    monkeypatch.setattr(images, "BaselineGeometry", counted)
    first, prepared = inspection_report(step, isolated_dir, text_budget=5000)
    assert all(sample["part_repetitions"] == 2 for sample in first["samples"]["features"])
    assert len(calls) == 1
    second, _ = inspection_report(step, isolated_dir, text_budget=5000)
    assert second == first
    assert len(calls) == 1
    (Path(second["inventory"]["image_root"]) / second["media"][-1]["png"]).write_bytes(b"partial write")
    inspection_report(step, isolated_dir, text_budget=5000)
    assert len(calls) == 2
    prepared["report"].write_text("{}", encoding="utf-8")
    rebuilt, _ = inspection_report(step, isolated_dir, images=False, text_budget=5000)
    assert rebuilt["analysis_revision"] == first["analysis_revision"]
    assert len(calls) == 3
    step.write_bytes(step.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Baseline does not match"):
        inspection_report(step, isolated_dir, images=False)


def test_cli_mcp_report_parity_and_real_image_content(runner, isolated_dir):
    from agentcad.mcp.server import inspect as mcp_inspect, mcp
    from mcp.types import CallToolResult

    step, _ = _baseline(isolated_dir)
    expected = _json(runner, ["inspect", str(step), "--report", "--max-images", "2", "--text-budget", "5000"])
    actual = mcp_inspect(file=str(step), cwd=str(isolated_dir), report=True, max_images=2, text_budget=5000)
    assert isinstance(actual, CallToolResult)
    assert actual.structuredContent.pop("_exit_code") == 0
    assert actual.structuredContent == expected
    assert [c.type for c in actual.content] == ["text", "image", "image"]
    assert base64.b64decode(actual.content[1].data) == (Path(expected["inventory"]["image_root"]) / expected["media"][0]["png"]).read_bytes()
    converted = asyncio.run(mcp.call_tool("inspect", {"file": str(step), "cwd": str(isolated_dir), "report": True, "max_images": 2}))
    assert isinstance(converted, CallToolResult)
    assert len(converted.content) == 3
    no_images = mcp_inspect(file=str(step), cwd=str(isolated_dir), report=True, report_images=False)
    assert isinstance(no_images, dict)
    assert no_images["media"] == []
    assert no_images["analysis_revision"] == expected["analysis_revision"]


def test_import_report_off_and_image_free_leave_analysis_retrievable(runner, isolated_dir):
    source = _nested_step(isolated_dir / "source.step")
    imported = _json(runner, ["import", str(source), "--init", "--no-view", "--no-diff", "--no-report", "--no-inspection-image"])
    assert imported["status"] == "success"
    assert "inspection" not in imported
    assert imported["artifacts"]["inspection_report"]["status"] == "success"
    assert imported["artifacts"]["inspection_image"]["status"] == "skipped"
    assert Path(imported["outputs"]["inspection_report"]).is_file()
    report = _json(runner, ["inspect", imported["outputs"]["step"], "--report", "--no-images"])
    assert report["quality"]["is_valid"] is True
    assert report["quality"]["status"] == "available"
    assert report["media_summary"]["state"] == "disabled"
    retrieved = _json(runner, ["inspect", imported["outputs"]["step"], "--report", "--max-images", "1"])
    assert retrieved["analysis_revision"] == report["analysis_revision"]
    assert len(retrieved["media"]) == 1
    # A neighbouring version's validation must not be adopted after files move.
    meta_path = Path(imported["outputs"]["step"]).with_name("meta.json")
    wrong = json.loads(meta_path.read_text())
    wrong["sha256"] = "different-original-source"
    meta_path.write_text(json.dumps(wrong), encoding="utf-8")
    unavailable = _json(runner, ["inspect", imported["outputs"]["step"], "--report", "--no-images"])
    assert unavailable["quality"]["status"] == "not_available"
    assert unavailable["analysis_revision"] != report["analysis_revision"]
    assert Path(report["inventory"]["report"]).is_file()


def test_image_failures_are_explicit_and_do_not_remove_report_or_core(runner, isolated_dir, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr("agentcad.render.render_geometry_sheet", broken)
    source = _nested_step(isolated_dir / "source.step")
    imported = _json(runner, ["import", str(source), "--init", "--no-view", "--no-diff"])
    assert imported["status"] == "success"
    assert imported["inspection"]["media_summary"]["state"] == "partial_failure"
    assert imported["inspection"]["media_summary"]["failed"] == 5
    assert Path(imported["outputs"]["step"]).is_file()
    assert Path(imported["outputs"]["inspection_report"]).is_file()
    assert (isolated_dir / "edit.py").is_file()
