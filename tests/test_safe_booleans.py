"""Validated Boolean helpers for fragile imported geometry."""

from pathlib import Path

import pytest
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt

import agentcad.helpers as helpers
from agentcad.helpers import safe_cut, safe_fuse, safe_intersection
from agentcad.step_io import load_cad_shape


FIXTURES = Path(__file__).parent / "fixtures" / "comparison"


def _box(dx=10, dy=10, dz=10, origin=(0, 0, 0)):
    return BRepPrimAPI_MakeBox(gp_Pnt(*origin), dx, dy, dz).Shape()


def test_safe_cut_applies_multiple_tools_without_mutating_source():
    source = _box(20, 20, 20)
    source_volume = helpers._shape_volume(source)
    first = _box(5, 20, 20, (0, 0, 0))
    second = _box(5, 20, 20, (15, 0, 0))

    result = safe_cut(source, first, second)

    assert BRepCheck_Analyzer(result).IsValid()
    assert helpers._shape_volume(result) == pytest.approx(4000.0)
    assert helpers._shape_volume(source) == pytest.approx(source_volume)
    assert not source.IsPartner(result)


def test_safe_intersection_never_exceeds_either_input():
    left = _box()
    right = _box(origin=(5, 0, 0))

    result = safe_intersection(left, right)

    assert BRepCheck_Analyzer(result).IsValid()
    assert helpers._shape_volume(result) == pytest.approx(500.0)
    assert helpers._shape_volume(result) <= helpers._shape_volume(left)
    assert helpers._shape_volume(result) <= helpers._shape_volume(right)


def test_safe_intersection_allows_valid_empty_result():
    result = safe_intersection(_box(), _box(origin=(20, 0, 0)))

    assert BRepCheck_Analyzer(result).IsValid()
    assert helpers._shape_volume(result) == pytest.approx(0.0)


def test_safe_fuse_handles_all_tools_in_one_operation():
    source = _box()
    right = _box(origin=(8, 0, 0))
    upper = _box(origin=(0, 8, 0))

    result = safe_fuse(source, right, upper)

    assert BRepCheck_Analyzer(result).IsValid()
    assert helpers._shape_volume(result) == pytest.approx(2600.0)
    assert helpers._shape_volume(source) == pytest.approx(1000.0)


def test_safe_booleans_canonicalize_overlapping_imported_compound():
    """Physical occupancy, not compound bookkeeping, drives validation."""
    single = load_cad_shape(FIXTURES / "single_solid.step")
    overlapping = load_cad_shape(FIXTURES / "overlapping_compound.step")

    assert helpers._shape_volume(overlapping) == pytest.approx(8216.0)
    assert helpers._physical_volume(overlapping, "fixture") == pytest.approx(8144.0)

    shared = safe_intersection(single, overlapping)
    merged = safe_fuse(single, overlapping)

    assert helpers._shape_volume(shared) == pytest.approx(8000.0)
    assert helpers._shape_volume(merged) == pytest.approx(8144.0)


def test_safe_fuse_uses_one_non_destructive_multi_tool_operation(monkeypatch):
    source = _box()
    first = _box(origin=(8, 0, 0))
    second = _box(origin=(0, 8, 0))
    operations = []

    class RecordingOperation:
        def __init__(self):
            self.perform_count = 0
            self.tool_count = 0
            self.non_destructive = False
            operations.append(self)

        def SetArguments(self, arguments):
            assert arguments.Size() == 1
            assert not source.IsPartner(arguments.First())

        def SetTools(self, tools):
            self.tool_count = tools.Size()

        def SetOperation(self, operation):
            self.operation = operation

        def SetNonDestructive(self, enabled):
            self.non_destructive = enabled

        def SetFuzzyValue(self, tolerance):
            self.tolerance = tolerance

        def Perform(self):
            self.perform_count += 1

        def HasErrors(self):
            return False

        def Shape(self):
            return _box()

        def DumpWarnings(self, output):
            pass

    monkeypatch.setattr(helpers, "BOPAlgo_BOP", RecordingOperation)

    safe_fuse(source, first, second)

    assert len(operations) == 1
    assert operations[0].perform_count == 1
    assert operations[0].tool_count == 2
    assert operations[0].non_destructive is True


@pytest.mark.parametrize("function", [safe_cut, safe_fuse])
def test_multi_tool_helpers_require_a_tool(function):
    with pytest.raises(ValueError, match="at least one"):
        function(_box())


@pytest.mark.parametrize("value", [-1, float("inf"), "not-a-number"])
def test_safe_boolean_rejects_invalid_tolerance(value):
    with pytest.raises(ValueError, match="finite non-negative"):
        safe_intersection(_box(), _box(), tolerance=value)


def test_safe_boolean_rejects_non_solid_input():
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Dir, gp_Pln

    face = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(), gp_Dir(0, 0, 1))).Face()
    with pytest.raises(ValueError, match="positive-volume solid geometry"):
        safe_cut(_box(), face)


def test_safe_cut_rejects_result_that_gains_volume(monkeypatch):
    monkeypatch.setattr(
        helpers,
        "_run_safe_boolean",
        lambda *args, **kwargs: _box(20, 20, 20),
    )

    with pytest.raises(ValueError, match="subtraction increased volume"):
        safe_cut(_box(), _box())


def test_safe_intersection_rejects_result_larger_than_input(monkeypatch):
    monkeypatch.setattr(
        helpers,
        "_run_safe_boolean",
        lambda *args, **kwargs: _box(20, 20, 20),
    )

    with pytest.raises(ValueError, match="exceeds input volume"):
        safe_intersection(_box(), _box())


def test_safe_fuse_rejects_result_outside_physical_range(monkeypatch):
    monkeypatch.setattr(
        helpers,
        "_run_safe_boolean",
        lambda *args, **kwargs: _box(1, 1, 1),
    )

    with pytest.raises(ValueError, match="outside the physical range"):
        safe_fuse(_box(), _box())


def test_safe_boolean_rejects_invalid_kernel_output(monkeypatch):
    class InvalidOperation:
        def SetArguments(self, arguments):
            pass

        def SetTools(self, tools):
            pass

        def SetOperation(self, operation):
            pass

        def SetNonDestructive(self, enabled):
            pass

        def SetFuzzyValue(self, tolerance):
            pass

        def Perform(self):
            pass

        def HasErrors(self):
            return False

        def Shape(self):
            from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon

            polygon = BRepBuilderAPI_MakePolygon()
            polygon.Add(gp_Pnt(0, 0, 0))
            polygon.Add(gp_Pnt(1, 0, 0))
            polygon.Add(gp_Pnt(0, 1, 0))
            polygon.Close()
            return polygon.Wire()

    monkeypatch.setattr(helpers, "BOPAlgo_BOP", InvalidOperation)

    with pytest.raises(ValueError, match="invalid geometry"):
        safe_cut(_box(), _box())


def test_safe_boolean_surfaces_kernel_error(monkeypatch):
    class ErrorOperation:
        def SetArguments(self, arguments):
            pass

        def SetTools(self, tools):
            pass

        def SetOperation(self, operation):
            pass

        def SetNonDestructive(self, enabled):
            pass

        def SetFuzzyValue(self, tolerance):
            pass

        def Perform(self):
            pass

        def HasErrors(self):
            return True

        def DumpErrors(self, output):
            output.write(b"simulated Boolean failure")

    monkeypatch.setattr(helpers, "BOPAlgo_BOP", ErrorOperation)

    with pytest.raises(ValueError, match="simulated Boolean failure"):
        safe_fuse(_box(), _box())
