"""Tests for the consolidated `load_cad_shape` helper (Phase 2 slice 2c)."""
from pathlib import Path

import cadquery as cq
import pytest
from cadquery import exporters

from agentcad.step_io import load_cad_shape


def _real_step(path: Path) -> Path:
    box = cq.Workplane("XY").box(10, 10, 10)
    exporters.export(box, str(path))
    return path


class TestLoadCadShape:
    def test_loads_step_returns_topods_shape(self, tmp_path):
        step = _real_step(tmp_path / "box.step")
        shape = load_cad_shape(step)
        # OCP TopoDS_Shape (or subclass) — duck-test via expected attributes
        assert shape is not None
        assert hasattr(shape, "IsNull")
        assert not shape.IsNull()

    def test_loads_brep(self, tmp_path):
        from OCP.BRepTools import BRepTools
        plate = cq.Workplane("XY").box(20, 20, 5)
        brep = tmp_path / "thing.brep"
        BRepTools.Write_s(plate.val().wrapped, str(brep))

        shape = load_cad_shape(brep)
        assert shape is not None
        assert not shape.IsNull()

    def test_missing_file_raises_clean_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            load_cad_shape(tmp_path / "nope.step")

    def test_unsupported_extension_raises_clean_error(self, tmp_path):
        """Caller should pre-screen via file_detect; this is a guard rail
        in case it doesn't, with a message pointing at the right tool."""
        path = tmp_path / "thing.stl"
        path.write_text("solid x\n")
        with pytest.raises(ValueError, match="file_detect"):
            load_cad_shape(path)

    def test_truncated_step_raises_clean_value_error(self, tmp_path):
        """The OCCT exception must NOT leak as a stack trace; caller catches
        ValueError and translates."""
        full = _real_step(tmp_path / "_full.step")
        truncated = tmp_path / "truncated.step"
        truncated.write_text(full.read_text()[: len(full.read_text()) // 2])
        with pytest.raises(ValueError):
            load_cad_shape(truncated)

    def test_empty_compound_raises_explanatory_error(self, tmp_path):
        """A STEP that parses but contains no solids/shells/faces is a real
        SolidWorks/Fusion failure mode (assembly stub, surfaces-only export).
        Must raise an error that names the cause."""
        # Synthesize: write a STEP from an empty Workplane.
        empty_step = tmp_path / "empty.step"
        # ISO-10303-21 envelope with no DATA entries — minimal valid file
        # that contains no solids.
        empty_step.write_text(
            "ISO-10303-21;\n"
            "HEADER;\n"
            "FILE_DESCRIPTION(('empty'),'2;1');\n"
            "FILE_NAME('empty','2026-01-01T00:00:00',(''),(''),'',' ',' ');\n"
            "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\n"
            "ENDSEC;\n"
            "DATA;\n"
            "ENDSEC;\n"
            "END-ISO-10303-21;\n"
        )
        with pytest.raises(ValueError) as exc_info:
            load_cad_shape(empty_step)
        # Error message must be agent-actionable, not just OCCT internals.
        msg = str(exc_info.value).lower()
        # The current empty-handling can land on either "null shape" (parser
        # produced nothing) or "no solids, shells, or faces" (empty compound).
        # Both are agent-friendly.
        assert ("empty" in msg or "null" in msg or "no solids" in msg
                or "incomplete" in msg or "corrupted" in msg)

    def test_accepts_str_path(self, tmp_path):
        step = _real_step(tmp_path / "box.step")
        shape = load_cad_shape(str(step))
        assert not shape.IsNull()

    def test_format_hint_loads_extensionless_step(self, tmp_path):
        step = _real_step(tmp_path / "box.step")
        blob = tmp_path / "661210dec702"
        step.rename(blob)

        shape = load_cad_shape(blob, format_hint="step")

        assert not shape.IsNull()

    def test_format_hint_loads_extensionless_brep(self, tmp_path):
        from OCP.BRepTools import BRepTools
        plate = cq.Workplane("XY").box(20, 20, 5)
        blob = tmp_path / "brep-blob"
        BRepTools.Write_s(plate.val().wrapped, str(blob))

        shape = load_cad_shape(blob, format_hint="brep")

        assert shape is not None
        assert not shape.IsNull()
