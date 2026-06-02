"""Round-trip tests for Phase 4a edit helpers — fillet_edges, chamfer_edges,
shell_faces. Each helper is exercised on a STEP imported via `agentcad import`,
then run through the b3d runner end-to-end so we verify the actual journey:
inspect --ids → pick by ID → apply edit → produce v2 with valid geometry.
"""
import json
from pathlib import Path

import cadquery as cq
import pytest
from cadquery import exporters

from agentcad.cli import cli


def _bracket_step(directory: Path, name: str = "bracket.step") -> Path:
    plate = cq.Workplane("XY").box(40, 30, 5)
    hole = cq.Workplane("XY").circle(5).extrude(10).translate((10, 0, 0))
    part = plate.cut(hole)  # Don't pre-fillet — agent will add fillets here
    path = directory / name
    exporters.export(part, str(path))
    return path


def _box_step(directory: Path, name: str = "box.step") -> Path:
    box = cq.Workplane("XY").box(40, 30, 10)
    path = directory / name
    exporters.export(box, str(path))
    return path


def _init_and_import(runner, isolated_dir, step_factory=_bracket_step, label="part"):
    runner.invoke(cli, ["init"])
    step = step_factory(isolated_dir)
    result = runner.invoke(cli, ["import", str(step), "--label", label])
    assert result.exit_code == 0, result.output


def _inspected_ids(runner, version_dir: str) -> dict:
    """Run `inspect --ids` and return the parsed JSON."""
    step = f"{version_dir}/output.step"
    result = runner.invoke(cli, ["inspect", step, "--ids"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _run_edit(runner, isolated_dir, script_text: str, output: str):
    """Write `edit.py` with given content, run it, return parsed JSON."""
    (isolated_dir / "edit.py").write_text(script_text)
    result = runner.invoke(cli, ["run", "edit.py", "--output", output])
    return result, (json.loads(result.stdout) if result.output else None)


# --- fillet_edges -----------------------------------------------------------

class TestFilletEdges:
    def test_fillet_single_edge_by_id(self, runner, isolated_dir):
        _init_and_import(runner, isolated_dir)
        ids = _inspected_ids(runner, "v1_part")
        # Pick the longest top-rim edge for filleting (deterministic-ish).
        top_edges = [
            e for e in ids["edges"]
            if all(p["z"] > 2 for p in e["endpoints"])
        ]
        assert top_edges, "expected at least one edge near the top of the bracket"
        edge_id = top_edges[0]["id"]

        result, parsed = _run_edit(
            runner, isolated_dir,
            f'''from build123d import *
base = load_step("v1_part/output.step")
result = fillet_edges(base, {edge_id}, 0.5)
show_object(result)
''',
            "filleted",
        )
        assert result.exit_code == 0, result.output
        assert parsed["status"] == "success"
        # Filleting adds at least one face (the rolled surface).
        assert parsed["metrics"]["face_count"] > ids["face_count"]
        assert parsed["metrics"]["is_valid"] is True

    def test_fillet_multiple_edges_by_id_list(self, runner, isolated_dir):
        _init_and_import(runner, isolated_dir)
        ids = _inspected_ids(runner, "v1_part")
        top_edges = [
            e for e in ids["edges"]
            if all(p["z"] > 2 for p in e["endpoints"])
        ][:3]
        edge_ids = [e["id"] for e in top_edges]
        assert len(edge_ids) >= 2, "need ≥2 top edges for the multi-id test"

        result, parsed = _run_edit(
            runner, isolated_dir,
            f'''from build123d import *
base = load_step("v1_part/output.step")
result = fillet_edges(base, {edge_ids}, 0.3)
show_object(result)
''',
            "many_filleted",
        )
        assert result.exit_code == 0, result.output
        assert parsed["status"] == "success"
        # Filleting N edges adds at least N rolled faces.
        assert parsed["metrics"]["face_count"] >= ids["face_count"] + len(edge_ids)


# --- chamfer_edges ---------------------------------------------------------

class TestChamferEdges:
    def test_chamfer_single_edge_by_id(self, runner, isolated_dir):
        _init_and_import(runner, isolated_dir, _box_step)
        ids = _inspected_ids(runner, "v1_part")
        # Box has 12 edges; pick the first.
        edge_id = ids["edges"][0]["id"]

        result, parsed = _run_edit(
            runner, isolated_dir,
            f'''from build123d import *
base = load_step("v1_part/output.step")
result = chamfer_edges(base, {edge_id}, 1.0)
show_object(result)
''',
            "chamfered",
        )
        assert result.exit_code == 0, result.output
        assert parsed["status"] == "success"
        # Chamfering removes some material → smaller volume.
        assert parsed["metrics"]["volume"] < ids["solids"][0]["volume"]
        assert parsed["metrics"]["is_valid"] is True


# --- shell_faces -----------------------------------------------------------

class TestShellFaces:
    def test_shell_open_top_face(self, runner, isolated_dir):
        """Hollow out the box, opening the top face — produces a cup/box-shell."""
        _init_and_import(runner, isolated_dir, _box_step)
        ids = _inspected_ids(runner, "v1_part")
        # Find the top face: highest centroid.z, normal ~+Z.
        top_face = max(ids["faces"], key=lambda f: f["centroid"]["z"])
        face_id = top_face["id"]

        result, parsed = _run_edit(
            runner, isolated_dir,
            f'''from build123d import *
base = load_step("v1_part/output.step")
result = shell_faces(base, {face_id}, -1.0)
show_object(result)
''',
            "shelled",
        )
        assert result.exit_code == 0, f"{result.output}\n{parsed}"
        assert parsed["status"] == "success"
        # Shelling carves out the interior → much smaller volume.
        assert parsed["metrics"]["volume"] < ids["solids"][0]["volume"] / 2
        assert parsed["metrics"]["is_valid"] is True


# --- error paths -----------------------------------------------------------

class TestShellFacesInnerLoopError:
    def test_shell_face_with_through_hole_gives_topology_aware_error(
        self, runner, isolated_dir
    ):
        """A through-hole pierces both top and bottom faces, leaving an inner
        loop on each. OCCT's MakeThickSolid can't shell those — error must
        say so explicitly, not the generic 'geometry too thin' message that
        masks the real cause (caught by the 4a friction-test)."""
        _init_and_import(runner, isolated_dir)  # bracket has a through-hole
        ids = _inspected_ids(runner, "v1_part")
        # Find the top face (highest centroid.z); it has the hole's inner loop.
        top_face = max(ids["faces"], key=lambda f: f["centroid"]["z"])

        result, parsed = _run_edit(
            runner, isolated_dir,
            f'''from build123d import *
base = load_step("v1_part/output.step")
result = shell_faces(base, {top_face["id"]}, -1.0)
show_object(result)
''',
            "should_fail",
        )
        assert result.exit_code != 0
        text = result.output.lower()
        assert "inner loop" in text or "through-hole" in text or "simply-connected" in text
        # Must not mislead with the "too thin" message in this case.
        assert "too thin" not in text


class TestEditHelperErrors:
    def test_fillet_out_of_range_id_raises_clean_error(self, runner, isolated_dir):
        _init_and_import(runner, isolated_dir)
        result, parsed = _run_edit(
            runner, isolated_dir,
            '''from build123d import *
base = load_step("v1_part/output.step")
result = fillet_edges(base, 99999, 0.5)
show_object(result)
''',
            "should_fail",
        )
        assert result.exit_code != 0
        text = result.output.lower()
        # Error must reference the offending ID and point at inspect --ids.
        assert "99999" in text or "out of range" in text
        assert "Traceback" not in result.output


# --- CQ-side validation ----------------------------------------------------

class TestCqScriptUsingEditHelpersFails:
    @pytest.mark.parametrize("helper", [
        "fillet_edges", "chamfer_edges", "shell_faces",
    ])
    def test_cq_script_using_helper_fails_with_kernel_neutral_hint(
        self, runner, isolated_dir, helper
    ):
        _init_and_import(runner, isolated_dir)
        (isolated_dir / "edit.py").write_text(
            f"""import cadquery as cq
from cadquery import importers
base = importers.importStep("v1_part/output.step")
result = {helper}(base, 1, 0.5)
show_object(result)
"""
        )
        result = runner.invoke(cli, ["run", "edit.py", "--output", "should_fail"])
        assert result.exit_code != 0
        text = result.output.lower()
        assert helper in text
        assert "build123d" in text or "importers" in text
