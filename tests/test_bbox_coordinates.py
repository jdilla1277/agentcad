"""Coordinate queries and recovery from common build123d API guesses."""

import json

import pytest
from build123d import Axis, Box, Compound, Part, Vector, export_step
from OCP.BRep import BRep_Builder
from OCP.TopoDS import TopoDS_Compound, TopoDS_Shape

from agentcad.api import bbox_point, bbox_size
from agentcad.cli import cli
from agentcad.commands.run import _execution_error_guidance
from agentcad.runners import build123d as b3d_runner
from agentcad.step_io import load_cad_shape


@pytest.fixture(params=["part", "raw", "imported"])
def shape(request, tmp_path):
    part = Part(Box(10, 20, 30).rotate(Axis.Z, 90).translate((-25, 40, -50)).wrapped)
    if request.param == "part":
        return part
    if request.param == "raw":
        return part.wrapped
    path = tmp_path / "source.step"
    export_step(part, path)
    return load_cad_shape(path)


def test_all_axis_coordinates_and_lengths(shape):
    assert bbox_point(shape, "min", "min", "min") == pytest.approx((-35, 35, -65))
    assert bbox_point(shape) == pytest.approx((-25, 40, -50))
    assert bbox_point(shape, "max", "max", "max") == pytest.approx((-15, 45, -35))
    assert bbox_point(shape, x="min", y="center", z="max") == pytest.approx((-35, 40, -35))
    assert bbox_size(shape) == pytest.approx((20, 10, 30))


def test_disconnected_compound_includes_gap_in_size():
    compound = Compound(children=[Box(2, 4, 6), Box(2, 4, 6).translate((10, 20, 30))])
    for shape in (compound, compound.wrapped):
        assert bbox_point(shape, "min", "min", "min") == pytest.approx((-1, -2, -3))
        assert bbox_point(shape, "max", "max", "max") == pytest.approx((11, 22, 33))
        assert bbox_point(shape) == pytest.approx((5, 10, 15))
        assert bbox_size(shape) == pytest.approx((12, 24, 36))


@pytest.mark.parametrize("helper", [bbox_point, bbox_size])
def test_empty_and_invalid_shapes_have_clear_errors(helper):
    with pytest.raises(TypeError, match="TopoDS_Shape"):
        helper(object())
    with pytest.raises(ValueError, match="null shape"):
        helper(TopoDS_Shape())
    empty = TopoDS_Compound()
    BRep_Builder().MakeCompound(empty)
    with pytest.raises(ValueError, match="empty shape"):
        helper(empty)


def test_injected_size_helper_and_explicit_import():
    result = b3d_runner.execute(
        "from agentcad.api import bbox_size as explicit_size\n"
        "shape = Box(2, 3, 4)\n"
        "assert bbox_size is explicit_size\n"
        "assert bbox_size(shape) == (2, 3, 4)\n"
        "assert bbox_size(shape.wrapped) == (2, 3, 4)\n"
        "show_object(shape)\n"
    )
    assert result.success, result.exception


@pytest.mark.parametrize("axis", "xyz")
@pytest.mark.parametrize("pattern,member,selector", [
    ("{}min", "min", "min"),
    ("lower_{}", "min", "min"),
    ("{}max", "max", "max"),
    ("upper_{}", "max", "max"),
    ("{}center", "center()", "center"),
    ("{}len", "size", "size"),
])
def test_flat_bbox_errors_give_executable_equivalents(axis, pattern, member, selector):
    shape = Box(10, 20, 30).translate((-5, 7, 12))
    bbox = shape.bounding_box()
    attr = pattern.format(axis)
    with pytest.raises(AttributeError) as caught:
        getattr(bbox, attr)
    guidance = _execution_error_guidance(str(caught.value), "build123d", "")
    expression = f"bbox.{member}.{axis.upper()}"
    assert f"`{expression}`" in guidance["suggestion"]
    coordinates = bbox_size(shape) if selector == "size" else bbox_point(shape, *([selector] * 3))
    assert eval(expression, {"bbox": bbox}) == pytest.approx(coordinates["xyz".index(axis)])
    assert guidance["more_at"] == "agentcad docs editing"


@pytest.mark.parametrize("axis", "xyz")
def test_ambiguous_bbox_axis_lists_all_choices(axis):
    with pytest.raises(AttributeError) as caught:
        getattr(Box(1, 2, 3).bounding_box(), axis)
    guidance = _execution_error_guidance(str(caught.value), "build123d", "")
    for member in ("min", "center()", "max", "size"):
        assert f"bbox.{member}.{axis.upper()}" in guidance["suggestion"]
    assert "ambiguous" in guidance["suggestion"]


@pytest.mark.parametrize("axis", "xyz")
def test_lowercase_vector_error(axis):
    with pytest.raises(AttributeError) as caught:
        getattr(Vector(1, 2, 3), axis)
    guidance = _execution_error_guidance(str(caught.value), "build123d", "")
    assert f"vector.{axis.upper()}" in guidance["suggestion"]


@pytest.mark.parametrize("message", [
    "'Report' object has no attribute 'xmin'",
    "'BoundBox' object has no attribute 'unknown'",
    "'Vector' object has no attribute 'lengths'",
])
def test_unrelated_attribute_errors_get_no_coordinate_guidance(message):
    assert _execution_error_guidance(message, "build123d", "") == {}


def test_coordinate_guidance_is_runtime_scoped():
    assert _execution_error_guidance("'Vector' object has no attribute 'x'", "cadquery", "") == {}


@pytest.mark.parametrize("expression,correction", [
    ("shape.bounding_box().lower_z", "bbox.min.Z"),
    ("shape.bounding_box().min.x", "vector.X"),
])
def test_cli_coordinate_error_persists_guidance(runner, isolated_dir, expression, correction):
    assert runner.invoke(cli, ["init", "--name", "coordinates"]).exit_code == 0
    script = isolated_dir / "edit.py"
    script.write_text(f"shape = Box(10, 20, 30)\nvalue = {expression}\nshow_object(shape)\n")
    result = runner.invoke(cli, ["run", str(script), "--label", "bounds", "--no-preview", "--no-daemon"])
    assert result.exit_code == 1, result.output
    output = json.loads(result.stdout)
    assert output["status"] == "failed"
    assert correction in output["suggestion"]
    meta = json.loads((isolated_dir / "v1_bounds_failed" / "meta.json").read_text())
    assert meta["suggestion"] == output["suggestion"]
    assert meta["more_at"] == output["more_at"] == "agentcad docs editing"
