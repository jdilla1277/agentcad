"""Mesh export functions for agentcad."""

import math

from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.Message import Message_ProgressRange
from OCP.RWGltf import RWGltf_CafWriter
from OCP.TColStd import TColStd_IndexedDataMapOfStringString
from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_ColorSurf, XCAFDoc_DocumentTool


_GLB_PALETTE = [
    (0.33, 0.55, 0.80),  # Steel blue
    (0.90, 0.35, 0.30),  # Coral red
    (0.30, 0.69, 0.31),  # Forest green
    (0.95, 0.70, 0.20),  # Gold
    (0.61, 0.35, 0.71),  # Purple
    (0.00, 0.59, 0.53),  # Teal
    (0.95, 0.50, 0.13),  # Orange
    (0.85, 0.44, 0.57),  # Pink
    (0.47, 0.33, 0.28),  # Brown
    (0.35, 0.76, 0.83),  # Sky blue
]


_NAMED_COLORS = {
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 0.50, 0.0),
    "blue": (0.0, 0.0, 1.0),
    "yellow": (1.0, 1.0, 0.0),
    "orange": (1.0, 0.55, 0.0),
    "purple": (0.50, 0.0, 0.50),
    "gray": (0.50, 0.50, 0.50),
    "grey": (0.50, 0.50, 0.50),
    "white": (1.0, 1.0, 1.0),
    "black": (0.0, 0.0, 0.0),
    "brown": (0.55, 0.27, 0.07),
    "pink": (1.0, 0.41, 0.71),
    "teal": (0.0, 0.50, 0.50),
    "gold": (1.0, 0.84, 0.0),
    "silver": (0.75, 0.75, 0.75),
    "cyan": (0.0, 1.0, 1.0),
    # Common agent/CSS names used in examples and friction runs.
    "steelblue": (0.27, 0.51, 0.71),
    "coral": (1.0, 0.50, 0.31),
    "forestgreen": (0.13, 0.55, 0.13),
}


def _parse_color(value):
    if value is None:
        return None
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in _NAMED_COLORS:
            return _NAMED_COLORS[raw]
        if raw.startswith("#") and len(raw) == 7:
            try:
                return tuple(int(raw[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
            except ValueError:
                return None
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            rgb = tuple(float(c) for c in value)
        except (TypeError, ValueError):
            return None
        if all(0.0 <= c <= 1.0 for c in rgb):
            return rgb
    return None


def _transform_y_up(shape):
    trsf = gp_Trsf()
    trsf.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)), -math.pi / 2)
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def _set_label_name(label, name):
    if name:
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(str(name)))


def export_glb(shape, output_path, linear_deflection=0.1, parts=None):
    """Export an OCP TopoDS_Shape to binary glTF (.glb).

    When `parts` is provided, each part's `topo_shape`, id/name, and requested
    color drive the GLB nodes/materials. Otherwise compounds are decomposed into
    individual solids and assigned fallback palette colors.
    """
    # Rotate Z-up (CadQuery/OCP) → Y-up (glTF standard).
    shape = _transform_y_up(shape)

    BRepMesh_IncrementalMesh(shape, linear_deflection)

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    app.InitDocument(doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    part_items = []
    if parts:
        for idx, part in enumerate(parts):
            part_shape = part.get("topo_shape")
            if part_shape is None:
                continue
            transformed = _transform_y_up(part_shape)
            BRepMesh_IncrementalMesh(transformed, linear_deflection)
            part_items.append({
                "shape": transformed,
                "name": part.get("id") or part.get("name") or f"part_{idx}",
                "color": _parse_color(part.get("color")),
            })

    if part_items:
        for i, item in enumerate(part_items):
            label = shape_tool.AddShape(item["shape"])
            _set_label_name(label, item["name"])
            r, g, b = item["color"] or _GLB_PALETTE[i % len(_GLB_PALETTE)]
            color = Quantity_Color(r, g, b, Quantity_TOC_RGB)
            color_tool.SetColor(label, color, XCAFDoc_ColorSurf)
    else:
        # Extract individual solids for fallback per-solid coloring.
        solids = []
        explorer = TopExp_Explorer(shape, TopAbs_SOLID)
        while explorer.More():
            solids.append(TopoDS.Solid_s(explorer.Current()))
            explorer.Next()

        if not solids:
            # No solids (e.g. shell/face) — add shape as-is, no color.
            shape_tool.AddShape(shape)
        else:
            for i, solid in enumerate(solids):
                label = shape_tool.AddShape(solid)
                r, g, b = _GLB_PALETTE[i % len(_GLB_PALETTE)]
                color = Quantity_Color(r, g, b, Quantity_TOC_RGB)
                color_tool.SetColor(label, color, XCAFDoc_ColorSurf)

    writer = RWGltf_CafWriter(TCollection_AsciiString(str(output_path)), True)
    writer.Perform(doc, TColStd_IndexedDataMapOfStringString(), Message_ProgressRange())


def _cross_normalize(ax, ay, az, bx, by, bz):
    """Cross product of two vectors, normalized."""
    cx = ay * bz - az * by
    cy = az * bx - ax * bz
    cz = ax * by - ay * bx
    length = math.sqrt(cx * cx + cy * cy + cz * cz)
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return (cx / length, cy / length, cz / length)


def export_obj(shape, output_path, linear_deflection=0.1):
    """Export an OCP TopoDS_Shape to Wavefront OBJ.

    Tessellates the shape, then walks faces to extract triangles.
    Computes per-face normals from triangle vertices.
    """
    BRepMesh_IncrementalMesh(shape, linear_deflection)

    vertices = []
    face_normals = []  # one normal per triangle
    triangles = []
    v_offset = 0

    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is None:
            explorer.Next()
            continue

        trsf = location.Transformation()
        nb_nodes = triangulation.NbNodes()
        nb_tris = triangulation.NbTriangles()

        # Extract vertices
        for i in range(1, nb_nodes + 1):
            node = triangulation.Node(i).Transformed(trsf)
            vertices.append((node.X(), node.Y(), node.Z()))

        # Extract triangles and compute per-face normals
        for i in range(1, nb_tris + 1):
            tri = triangulation.Triangle(i)
            n1, n2, n3 = tri.Get()
            i1 = n1 + v_offset
            i2 = n2 + v_offset
            i3 = n3 + v_offset
            triangles.append((i1, i2, i3))

            # Compute face normal from vertices
            v0 = vertices[i1 - 1]
            v1 = vertices[i2 - 1]
            v2 = vertices[i3 - 1]
            normal = _cross_normalize(
                v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2],
                v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2],
            )
            face_normals.append(normal)

        v_offset += nb_nodes
        explorer.Next()

    with open(output_path, "w") as f:
        f.write("# agentcad OBJ export\n")
        for v in vertices:
            f.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
        for n in face_normals:
            f.write(f"vn {n[0]:.6f} {n[1]:.6f} {n[2]:.6f}\n")
        for idx, tri in enumerate(triangles):
            ni = idx + 1  # 1-based normal index
            f.write(f"f {tri[0]}//{ni} {tri[1]}//{ni} {tri[2]}//{ni}\n")
