import json

import cadquery as cq
from cadquery import exporters

from agentcad.cli import cli


def _make_step(directory, name="output.step"):
    box = cq.Workplane("XY").box(10, 10, 10)
    path = directory / name
    exporters.export(box, str(path))
    return path


class TestInspectCommand:
    def test_inspect_returns_json(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["command"] == "inspect"
        assert parsed["status"] == "success"

    def test_inspect_reports_solid_count(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.output)
        assert parsed["solid_count"] == 1

    def test_inspect_reports_shell_count(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.output)
        assert parsed["shell_count"] == 1

    def test_inspect_reports_face_count(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.output)
        assert parsed["face_count"] == 6

    def test_inspect_reports_edge_count(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.output)
        assert parsed["edge_count"] == 12

    def test_inspect_shell_details(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.output)
        shells = parsed["shells"]
        assert len(shells) == 1
        assert shells[0]["closed"] is True
        assert shells[0]["face_count"] == 6

    def test_inspect_face_orientations(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.output)
        orient = parsed["face_orientations"]
        assert orient["forward"] + orient["reversed"] == 6

    def test_inspect_missing_file_error(self, runner, isolated_dir):
        result = runner.invoke(cli, ["inspect", "nope.step"])
        assert result.exit_code == 1
        parsed = json.loads(result.output)
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
        parsed = json.loads(result.output)
        assert parsed["solid_count"] == 2
        assert parsed["shell_count"] == 2

    def test_inspect_is_valid(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.output)
        assert parsed["is_valid"] is True

    def test_inspect_free_edge_count(self, runner, isolated_dir):
        """A closed box has zero free edges."""
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.output)
        assert parsed["free_edge_count"] == 0
