import json

import cadquery as cq
from cadquery import exporters

from agentcad.cli import cli


def _make_step(directory, name="output.step"):
    box = cq.Workplane("XY").box(10, 10, 10)
    path = directory / name
    exporters.export(box, str(path))
    return path


def _invalid_large_brep(directory, name="invalid_large.brep", count=600):
    """A CADGenBench-202-class input: compound of self-intersecting (bowtie)
    faces — invalid topology, large face/edge count, many free edges.

    Built via raw OCP and written to BREP because cadquery/OCCT heal simple
    invalidity on a STEP round-trip; a BREP compound of bowtie faces survives
    as genuinely invalid (BRepCheck reports SelfIntersectingWire +
    UnorientableShape, the same classes the issue observed)."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
    )
    from OCP.BRepTools import BRepTools
    from OCP.gp import gp_Pnt
    from OCP.TopoDS import TopoDS_Compound

    builder = BRep_Builder()
    comp = TopoDS_Compound()
    builder.MakeCompound(comp)
    for i in range(count):
        dx, dy = (i % 30) * 30.0, (i // 30) * 30.0
        poly = BRepBuilderAPI_MakePolygon()
        for x, y in [(0, 0), (10, 10), (10, 0), (0, 10)]:
            poly.Add(gp_Pnt(x + dx, y + dy, 0))
        poly.Close()
        builder.Add(comp, BRepBuilderAPI_MakeFace(poly.Wire(), True).Face())
    path = directory / name
    BRepTools.Write_s(comp, str(path))
    return path


class TestInspectCommand:
    def test_inspect_returns_json(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed["command"] == "inspect"
        assert parsed["status"] == "success"

    def test_inspect_reports_solid_count(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        assert parsed["solid_count"] == 1

    def test_inspect_reports_shell_count(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        assert parsed["shell_count"] == 1

    def test_inspect_reports_face_count(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        assert parsed["face_count"] == 6

    def test_inspect_reports_edge_count(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        assert parsed["edge_count"] == 12

    def test_inspect_shell_details(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        shells = parsed["shells"]
        assert len(shells) == 1
        assert shells[0]["closed"] is True
        assert shells[0]["face_count"] == 6

    def test_inspect_face_orientations(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        orient = parsed["face_orientations"]
        assert orient["forward"] + orient["reversed"] == 6

    def test_inspect_missing_file_error(self, runner, isolated_dir):
        result = runner.invoke(cli, ["inspect", "nope.step"])
        assert result.exit_code == 1
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "error"

    def test_inspect_compound(self, runner, isolated_dir):
        """Compound with 2 solids should report correctly."""
        box1 = cq.Workplane("XY").box(10, 10, 10)
        box2 = cq.Workplane("XY").workplane(offset=20).box(5, 5, 5)
        compound = cq.Compound.makeCompound([box1.val(), box2.val()])
        wp = cq.Workplane("XY").newObject([compound])
        step = isolated_dir / "compound.step"
        exporters.export(wp, str(step))

        result = runner.invoke(cli, ["inspect", str(step)])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed["solid_count"] == 2
        assert parsed["shell_count"] == 2

    def test_inspect_is_valid(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        assert parsed["is_valid"] is True

    def test_inspect_free_edge_count(self, runner, isolated_dir):
        """A closed box has zero free edges."""
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        assert parsed["free_edge_count"] == 0

    def test_inspect_loads_step_symlink_to_extensionless_target(
        self, runner, isolated_dir
    ):
        step = _make_step(isolated_dir, "source.step")
        blob = isolated_dir / "661210dec702"
        link = isolated_dir / "input.step"
        step.rename(blob)
        link.symlink_to(blob.name)

        result = runner.invoke(cli, ["inspect", str(link), "--no-daemon"])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        assert parsed["file"].endswith("input.step")
        assert parsed["format_detected"] == "step"
        assert parsed["face_count"] == 6


# --- Edit-risk classification (#32) ---

class TestInspectEditRisk:
    def test_clean_part_has_no_edit_risk(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step), "--no-daemon"])
        parsed = json.loads(result.stdout)
        assert "edit_risk" not in parsed
        assert "risk_reasons" not in parsed

    def test_large_part_flagged_with_guidance(self, runner, isolated_dir):
        # 200 separate boxes -> 1200 faces, over the large-topology threshold.
        solids = []
        for i in range(200):
            solids.append(cq.Workplane("XY").transformed(offset=(i * 20, 0, 0)).box(10, 10, 10).val())
        compound = cq.Compound.makeCompound(solids)
        wp = cq.Workplane("XY").newObject([compound])
        step = isolated_dir / "big.step"
        exporters.export(wp, str(step))

        result = runner.invoke(cli, ["inspect", str(step), "--no-daemon"])
        assert result.exit_code == 0, result.stdout
        parsed = json.loads(result.stdout)
        assert parsed["face_count"] >= 1000
        assert parsed["edit_risk"] == "medium"  # large but valid
        assert any("large topology" in r for r in parsed["risk_reasons"])
        assert parsed["recommended_workflow"]

    def test_invalid_large_input_flagged_high_end_to_end(self, runner, isolated_dir):
        """CADGenBench-202-class input at the real CLI boundary: invalid +
        large + many free edges -> high, with validity_errors surfaced and
        next_actions kept coherent with the workflow (no normal-editing
        suggestions that contradict it)."""
        brep = _invalid_large_brep(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(brep), "--no-daemon"])
        assert result.exit_code == 0, result.stdout
        parsed = json.loads(result.stdout)
        assert parsed["is_valid"] is False
        assert "BRepCheck_UnorientableShape" in parsed["validity_errors"]
        assert parsed["edit_risk"] == "high"
        workflow = " ".join(parsed["recommended_workflow"]).lower()
        assert "load_step" in workflow and "boolean" in workflow
        # next_actions must not contradict the workflow with cheerful
        # normal-editing follow-ups.
        next_joined = " ".join(parsed["next_actions"]).lower()
        assert "recommended_workflow" in next_joined
        assert "hole diameters" not in next_joined

    def test_high_risk_brep_symlink_to_extensionless_target_loads(
        self, runner, isolated_dir
    ):
        brep = _invalid_large_brep(isolated_dir, "source.brep")
        blob = isolated_dir / "661210dec702"
        link = isolated_dir / "input.brep"
        brep.rename(blob)
        link.symlink_to(blob.name)

        result = runner.invoke(cli, ["inspect", str(link), "--no-daemon"])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        assert parsed["file"].endswith("input.brep")
        assert parsed["format_detected"] == "brep"
        assert parsed["edit_risk"] == "high"


# --- Daemon routing (#177) ---

class TestInspectDaemonRouting:
    """`agentcad inspect` routes through the daemon when one is running,
    same contract as `agentcad run`. Without this, a workflow that runs
    `run` → `inspect` pays the OCP startup cost twice."""

    def test_inspect_routes_through_daemon_when_available(self, runner, isolated_dir, monkeypatch):
        monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
        daemon_output = json.dumps({
            "command": "inspect",
            "status": "success",
            "solid_count": 1, "shell_count": 1,
            "face_count": 6, "edge_count": 12, "is_valid": True,
        })
        monkeypatch.setattr(
            "agentcad.daemon.send_request",
            lambda *a, **kw: {"type": "result", "exit_code": 0, "output": daemon_output},
        )
        result = runner.invoke(cli, ["inspect", "any.step"])
        assert result.exit_code == 0, result.stdout
        parsed = json.loads(result.stdout)
        assert parsed["via"] == "daemon"
        assert parsed["command"] == "inspect"

    def test_inspect_direct_no_via_field(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        assert "via" not in parsed

    def test_inspect_no_daemon_flag_skips_routing(self, runner, isolated_dir, monkeypatch):
        step = _make_step(isolated_dir)
        monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
        calls: list[tuple] = []
        def _track(*a, **kw):
            calls.append((a, kw))
            return None
        monkeypatch.setattr("agentcad.daemon.send_request", _track)
        result = runner.invoke(cli, ["inspect", str(step), "--no-daemon"])
        assert result.exit_code == 0, result.stdout
        parsed = json.loads(result.stdout)
        assert "via" not in parsed
        assert calls == [], f"send_request was called despite --no-daemon: {calls}"
