from pathlib import Path

import cadquery as cq

from cadtool.export import export_glb


def _make_box():
    return cq.Workplane("XY").box(10, 10, 10).val().wrapped


def _make_cylinder():
    return cq.Workplane("XY").cylinder(10, 5).val().wrapped


def test_export_glb_produces_file(tmp_path):
    out = tmp_path / "output.glb"
    export_glb(_make_box(), str(out))
    assert out.exists()
    assert out.stat().st_size > 0


def test_export_glb_valid_magic_bytes(tmp_path):
    out = tmp_path / "output.glb"
    export_glb(_make_box(), str(out))
    magic = out.read_bytes()[:4]
    assert magic == b"glTF"


def test_export_glb_different_shapes_differ(tmp_path):
    box_path = tmp_path / "box.glb"
    cyl_path = tmp_path / "cyl.glb"
    export_glb(_make_box(), str(box_path))
    export_glb(_make_cylinder(), str(cyl_path))
    assert box_path.read_bytes() != cyl_path.read_bytes()
