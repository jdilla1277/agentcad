import json
import struct
from pathlib import Path

import cadquery as cq

from cadtool.export import export_glb, export_obj


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


# --- OBJ export tests ---


def test_export_obj_produces_file(tmp_path):
    out = tmp_path / "output.obj"
    export_obj(_make_box(), str(out))
    assert out.exists()
    assert out.stat().st_size > 0


def test_export_obj_has_vertices_and_faces(tmp_path):
    out = tmp_path / "output.obj"
    export_obj(_make_box(), str(out))
    text = out.read_text()
    lines = text.strip().split("\n")
    v_lines = [l for l in lines if l.startswith("v ")]
    vn_lines = [l for l in lines if l.startswith("vn ")]
    f_lines = [l for l in lines if l.startswith("f ")]
    assert len(v_lines) > 0, "OBJ should have vertices"
    assert len(vn_lines) > 0, "OBJ should have normals"
    assert len(f_lines) > 0, "OBJ should have faces"


def test_export_obj_faces_are_triangles(tmp_path):
    out = tmp_path / "output.obj"
    export_obj(_make_box(), str(out))
    text = out.read_text()
    f_lines = [l for l in text.strip().split("\n") if l.startswith("f ")]
    for line in f_lines:
        parts = line.split()[1:]  # skip "f"
        assert len(parts) == 3, f"Expected triangles, got {len(parts)} vertices: {line}"


def test_export_obj_different_shapes_differ(tmp_path):
    box_path = tmp_path / "box.obj"
    cyl_path = tmp_path / "cyl.obj"
    export_obj(_make_box(), str(box_path))
    export_obj(_make_cylinder(), str(cyl_path))
    assert box_path.read_text() != cyl_path.read_text()


# --- Multi-solid GLB export tests ---


def _parse_glb_json(path):
    """Parse the JSON chunk from a binary glTF file."""
    data = Path(path).read_bytes()
    # 12-byte header: magic(4) + version(4) + length(4)
    # JSON chunk: length(4) + type(4) + data
    json_length = struct.unpack("<I", data[12:16])[0]
    return json.loads(data[20:20 + json_length])


def _make_compound():
    """Two non-touching solids in a compound."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.TopoDS import TopoDS_Compound, TopoDS_Builder
    from OCP.gp import gp_Pnt, gp_Ax2, gp_Dir
    box = BRepPrimAPI_MakeBox(10, 10, 10).Shape()
    cyl = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(50, 0, 0), gp_Dir(0, 0, 1)), 5, 20).Shape()
    builder = TopoDS_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    builder.Add(compound, box)
    builder.Add(compound, cyl)
    return compound


def test_export_glb_multi_solid_distinct_meshes(tmp_path):
    out = tmp_path / "output.glb"
    export_glb(_make_compound(), str(out))
    gltf = _parse_glb_json(out)
    assert len(gltf["meshes"]) >= 2


def test_export_glb_multi_solid_distinct_materials(tmp_path):
    out = tmp_path / "output.glb"
    export_glb(_make_compound(), str(out))
    gltf = _parse_glb_json(out)
    assert len(gltf["materials"]) >= 2


def test_export_glb_multi_solid_colors_differ(tmp_path):
    out = tmp_path / "output.glb"
    export_glb(_make_compound(), str(out))
    gltf = _parse_glb_json(out)
    colors = [
        tuple(m["pbrMetallicRoughness"]["baseColorFactor"])
        for m in gltf["materials"]
    ]
    assert len(set(colors)) >= 2, f"Expected distinct colors, got {colors}"
