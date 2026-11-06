"""Optional USDZ export for Apple Quick Look and other USD viewers.

USDZ support ships in the ``usdz`` extra (``pip install "agentcad[usdz]"``)
because OpenUSD (``usd-core``, imported as ``pxr``) is a large native
dependency with its own license. Nothing here imports ``pxr`` at module
load; the default install never loads OpenUSD.

The package is a static CAD handoff: one binary ``.usdc`` layer with a
triangle mesh per captured part (or per solid for STEP-only input),
UsdPreviewSurface colors, Y-up orientation, and millimetre units. STEP stays
the precise CAD deliverable; USDZ is a tessellated presentation artifact.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

USDZ_INSTALL_COMMAND = 'pip install "agentcad[usdz]"'
# AgentCAD geometry is authored in millimetres; AR viewers read this to place
# the model at real-world size.
USDZ_METERS_PER_UNIT = 0.001
# Root prim and the scope that holds one material per mesh.
ROOT_PRIM = "Model"
MATERIALS_SCOPE = "Materials"
# The package's single layer. Fixed so no source or temp path leaks inside.
LAYER_NAME = "model.usdc"

_DIAGNOSTIC_LIMIT = 500


def _next_actions(install_command):
    """The install command, plus a daemon restart only when this
    environment's daemon socket exists (a running daemon keeps old imports)."""
    actions = [install_command]
    try:
        from agentcad.daemon import _default_socket_path
        if os.path.exists(_default_socket_path()):
            actions.append("agentcad daemon restart")
    except Exception:  # no daemon support on this platform
        pass
    return actions


def usdz_unavailable_payload(command):
    """Return a structured error payload when USDZ export cannot run, else None.

    Commands call this before loading geometry, running a script, allocating
    a version, routing to the daemon, or writing any other requested format.
    """
    try:
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, UsdUtils, Vt  # noqa: F401
    except ModuleNotFoundError as exc:
        # Only a missing top-level package means the extra is absent; a
        # missing native submodule (pxr.Usd) is a broken install.
        if exc.name == "pxr":
            return {
                "command": command,
                "status": "error",
                "error_kind": "optional_dependency_missing",
                "failed_format": "usdz",
                "message": (
                    "USDZ export is not installed (it needs OpenUSD's usd-core "
                    "package). Install it with "
                    f"`{USDZ_INSTALL_COMMAND}` in the same environment as "
                    "AgentCAD, then run `agentcad daemon restart` if a daemon "
                    "is running. Other formats (stl, glb, obj) need no extra."
                ),
                "install_command": USDZ_INSTALL_COMMAND,
                "python": sys.executable,
                "next_actions": _next_actions(USDZ_INSTALL_COMMAND),
            }
        broken = exc
    except Exception as exc:  # native loader failures raise ImportError/OSError
        broken = exc
    else:
        return None
    return {
        "command": command,
        "status": "error",
        "error_kind": "optional_dependency_broken",
        "failed_format": "usdz",
        "message": (
            "USDZ support is installed but OpenUSD failed to load. Reinstall "
            f"it with `pip install --force-reinstall \"agentcad[usdz]\"` in "
            "the same environment as AgentCAD, then run "
            "`agentcad daemon restart` if a daemon is running."
        ),
        "diagnostic": f"{type(broken).__name__}: {broken}"[:_DIAGNOSTIC_LIMIT],
        "install_command": 'pip install --force-reinstall "agentcad[usdz]"',
        "python": sys.executable,
        "next_actions": _next_actions('pip install --force-reinstall "agentcad[usdz]"'),
    }


def _mesh_items(shape, parts):
    """Split the export into named, colored bodies, mirroring GLB export."""
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS, TopoDS_Compound

    from agentcad.export import _GLB_PALETTE, _parse_color

    items = []
    if parts:
        for idx, part in enumerate(parts):
            if part.get("topo_shape") is None:
                continue
            items.append({
                "shape": part["topo_shape"],
                "name": str(part.get("id") or part.get("name") or f"part_{idx}"),
                "color": _parse_color(part.get("color")),
            })
    if not items:
        solids = []
        explorer = TopExp_Explorer(shape, TopAbs_SOLID)
        while explorer.More():
            solids.append(TopoDS.Solid_s(explorer.Current()))
            explorer.Next()
        if not solids:
            items.append({"shape": shape, "name": "shape", "color": None})
        else:
            for i, solid in enumerate(solids, start=1):
                items.append({"shape": solid, "name": f"solid_{i}", "color": None})
            # Like GLB: loose faces outside solids are exported, never dropped,
            # so a file cannot look closed only because surfaces vanished.
            builder = BRep_Builder()
            loose = TopoDS_Compound()
            builder.MakeCompound(loose)
            has_loose = False
            explorer = TopExp_Explorer(shape, TopAbs_FACE, TopAbs_SOLID)
            while explorer.More():
                builder.Add(loose, explorer.Current())
                has_loose = True
                explorer.Next()
            if has_loose:
                items.append({"shape": loose, "name": "loose_faces", "color": None})
    for i, item in enumerate(items):
        if item["color"] is None:
            item["color"] = _GLB_PALETTE[i % len(_GLB_PALETTE)]
    return items


def _prim_names(names):
    """Legal, unique USD prim identifiers, in order, for the given part names."""
    from pxr import Tf

    used = {MATERIALS_SCOPE}
    result = []
    for name in names:
        base = Tf.MakeValidIdentifier(name) or "part"
        candidate, n = base, 2
        while candidate in used:
            candidate, n = f"{base}_{n}", n + 1
        used.add(candidate)
        result.append(candidate)
    return result


def _tessellate(item_shape, linear_deflection):
    """Y-up points, flat triangle indices, and vertex normals for one body."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepMesh import BRepMesh_IncrementalMesh

    from agentcad.export import face_triangulations

    # Mesh a copy, as GLB export does: an earlier, finer tessellation left on
    # the shape (e.g. by validation) would otherwise be reused, making the
    # output depend on what ran before instead of on linear_deflection.
    item_shape = BRepBuilderAPI_Copy(item_shape, True, False).Shape()
    BRepMesh_IncrementalMesh(item_shape, linear_deflection)
    points, indices, normals = [], [], []
    for face_points, triangles, face_normals in face_triangulations(item_shape, with_normals=True):
        offset = len(points)
        # Z-up (OCP) -> Y-up, the same -90 degree X rotation as GLB export.
        # It is a proper rotation, so triangle winding is unchanged.
        points.extend((x, z, -y) for x, y, z in face_points)
        normals.extend((x, z, -y) for x, y, z in face_normals)
        for tri in triangles:
            indices.extend(i + offset for i in tri)
    return points, indices, normals


def _pin_zip_timestamps(path):
    """Zero every ZIP member timestamp (1980-01-01) so identical geometry
    gives identical bytes and sha256. Timestamps sit outside the CRC-covered
    data and the 64-byte alignment, so nothing else changes."""
    import struct
    import zipfile

    data = bytearray(Path(path).read_bytes())
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        offset = archive.start_dir
    epoch = struct.pack("<HH", 0, 0x21)  # DOS time 00:00:00, date 1980-01-01
    for info in infos:
        data[info.header_offset + 10:info.header_offset + 14] = epoch
    for _ in infos:
        if struct.unpack_from("<I", data, offset)[0] != 0x02014B50:
            raise RuntimeError("Unexpected USDZ central directory layout.")
        data[offset + 12:offset + 16] = epoch
        name, extra, comment = struct.unpack_from("<HHH", data, offset + 28)
        offset += 46 + name + extra + comment
    Path(path).write_bytes(bytes(data))


def export_usdz(shape, output_path, linear_deflection=0.1, parts=None):
    """Export an OCP TopoDS_Shape to a self-contained USDZ package.

    ``parts`` follows :func:`agentcad.export.export_glb`: each captured part
    becomes one mesh prim named after its id and colored by its requested
    color, falling back to the GLB palette. Without parts, each solid (plus
    any loose faces) becomes its own mesh prim. The destination appears only
    after the stage and package are complete; temporary files are removed on
    success and failure. Requires ``agentcad[usdz]``.
    """
    from pxr import Gf, Kind, Sdf, Usd, UsdGeom, UsdShade, UsdUtils, Vt

    from agentcad import __version__

    output_path = Path(output_path)
    items = _mesh_items(shape, parts)
    names = _prim_names([item["name"] for item in items])

    # ignore_cleanup_errors: on Windows OpenUSD may still hold the staged layer
    # open; a leftover temp dir must not turn a published export into an error.
    with tempfile.TemporaryDirectory(prefix=".agentcad-usdz-", dir=output_path.parent,
                                     ignore_cleanup_errors=True) as temp:
        layer_path = Path(temp) / LAYER_NAME
        stage = Usd.Stage.CreateNew(str(layer_path))
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, USDZ_METERS_PER_UNIT)
        root = UsdGeom.Xform.Define(stage, f"/{ROOT_PRIM}")
        Usd.ModelAPI(root.GetPrim()).SetKind(Kind.Tokens.component)
        stage.SetDefaultPrim(root.GetPrim())
        stage.GetRootLayer().customLayerData = {"creator": f"agentcad {__version__}"}
        UsdGeom.Scope.Define(stage, f"/{ROOT_PRIM}/{MATERIALS_SCOPE}")

        for item, prim_name in zip(items, names):
            points, indices, normals = _tessellate(item["shape"], linear_deflection)
            mesh = UsdGeom.Mesh.Define(stage, f"/{ROOT_PRIM}/{prim_name}")
            point_array = Vt.Vec3fArray([Gf.Vec3f(*p) for p in points])
            mesh.CreatePointsAttr(point_array)
            mesh.CreateFaceVertexCountsAttr(Vt.IntArray([3] * (len(indices) // 3)))
            mesh.CreateFaceVertexIndicesAttr(Vt.IntArray(indices))
            mesh.CreateNormalsAttr(Vt.Vec3fArray([Gf.Vec3f(*n) for n in normals]))
            mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
            mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
            if points:
                mesh.CreateExtentAttr(UsdGeom.PointBased.ComputeExtent(point_array))
            rgb = Gf.Vec3f(*item["color"])
            mesh.CreateDisplayColorAttr(Vt.Vec3fArray([rgb]))
            prim = mesh.GetPrim()
            # Keep the original AgentCAD part id even when the prim name had
            # to be sanitized or de-duplicated.
            prim.SetCustomDataByKey("agentcad:part_id", item["name"])
            if prim_name != item["name"]:
                prim.SetDisplayName(item["name"])

            material_path = f"/{ROOT_PRIM}/{MATERIALS_SCOPE}/{prim_name}"
            material = UsdShade.Material.Define(stage, material_path)
            shader = UsdShade.Shader.Define(stage, f"{material_path}/PreviewSurface")
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(rgb)
            shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.5)
            shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
            material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(material)

        if not stage.GetRootLayer().Save():
            raise RuntimeError("OpenUSD could not write the USD stage.")
        del stage, root

        staged = Path(temp) / "package.usdz"
        if not UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(layer_path)), str(staged)):
            raise RuntimeError("OpenUSD could not package the USDZ file.")
        if not staged.is_file():
            raise RuntimeError("OpenUSD did not produce a USDZ package.")
        _pin_zip_timestamps(staged)
        os.replace(staged, output_path)
