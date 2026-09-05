#!/usr/bin/env python3
"""Generate the public validation fixtures for M71 (total validation).

Each fixture is a small, original, synthetic shape that reproduces one class
of disagreement between AgentCAD's kernel-only ``is_valid`` and the validity
gate downstream consumers apply (closed shells plus a closed orientable
manifold mesh). They are generated from source so they can live in the public
repository, and a test pins the checked-in bytes to this generator.

See ``tests/fixtures/validation/catalog.json`` for what each case expects.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import cadquery as cq
from cadquery import exporters
from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_Sewing,
)
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepTools import BRepTools
from OCP.gp import gp_Pnt, gp_Vec
from OCP.TopoDS import TopoDS_Compound


FIXED_STEP_TIMESTAMP = "2000-01-01T00:00:00"


def _box(size=20.0, at=(0.0, 0.0, 0.0)):
    return cq.Workplane("XY").box(size, size, size).translate(at).val()


def _compound(*shapes):
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape.wrapped)
    return cq.Shape.cast(compound)


def closed_box():
    """Control: one closed 20 mm cube. Passes every layer."""
    return _box()


def open_shell():
    """A 20 mm cube with its +Z face removed: five faces, one open shell.

    Kernel-consistent, so ``BRepCheck`` passes, but not watertight. Its
    "volume" is meaningless.
    """
    box = _box()
    faces = [f for f in box.Faces() if f.Center().z < 9.9]
    return cq.Shell.makeShell(faces)


def touching_edge_solids():
    """Two 10 mm cubes that touch only along one vertical edge, fused.

    The kernel keeps them as two closed solids with no shared topology, and
    the downstream gate meshes them as two clean bodies. This is a guard
    against over-strictness: touching bodies are deliverable.
    """
    a = _box(10, (0, 0, 0))
    b = _box(10, (10, 10, 0))
    return a.fuse(b)


def touching_vertex_solids():
    """Two 10 mm cubes that touch only at one corner, fused. Same guard."""
    a = _box(10, (0, 0, 0))
    c = _box(10, (10, 10, 10))
    return a.fuse(c)


def nonmanifold_sewn_cubes():
    """The same two edge-touching cubes, sewn in non-manifold mode.

    Sewing merges the coincident edge into one edge carried by four faces.
    After a STEP round-trip the kernel reports two closed shells and calls
    the result valid, but the tessellated mesh has an edge shared by four
    triangles: the exact condition that rejected CADGenBench fixture 250.
    """
    sewing = BRepBuilderAPI_Sewing(1e-6)
    sewing.SetNonManifoldMode(True)
    for cube in (_box(10, (0, 0, 0)), _box(10, (10, 10, 0))):
        for face in cube.Faces():
            sewing.Add(face.wrapped)
    sewing.Perform()
    return cq.Shape.cast(sewing.SewedShape())


def loose_face_compound():
    """A closed cube plus an unattached 5 mm square face in one compound.

    Volume and validity come from the cube; the loose face is silently
    ignored by every current metric.
    """
    box = _box()
    face = cq.Face.makePlane(5, 5, basePnt=cq.Vector(40, 0, 0))
    return _compound(box, face)


def overlapping_members():
    """Two cubes that overlap, kept as two compound members, not fused.

    Every layer passes, but summing member volumes double-counts the overlap.
    """
    a = _box(20, (0, 0, 0))
    b = _box(20, (10, 0, 0))
    return _compound(a, b)


def disjoint_solids():
    """Two closed cubes 30 mm apart in one compound.

    Deliverable as geometry; the structure layer reports two solids so a
    declared expectation of one can fail.
    """
    a = _box(10, (0, 0, 0))
    b = _box(10, (40, 0, 0))
    return _compound(a, b)


def nonmanifold_edge_prism():
    """A bow-tie profile extruded through the modeling API.

    The kernel splits the crossing into one solid whose faces meet four at
    the crossing edge, with zero net volume, and calls it valid. The mesh
    gate rejects the four-triangle edge.
    """
    return (
        cq.Workplane("XY")
        .polyline([(0, 0), (20, 20), (20, 0), (0, 20)])
        .close()
        .extrude(5)
        .val()
    )


def bowtie_prism_invalid():
    """A bow-tie face built directly from its self-intersecting wire, extruded.

    The kernel checker rejects this (self-intersecting wire, unorientable
    shape). STEP export heals it, so it is written as BREP, which preserves
    the defect. The kernel-check layer needs an input that fails.
    """
    polygon = BRepBuilderAPI_MakePolygon()
    for x, y in [(0, 0), (20, 20), (20, 0), (0, 20)]:
        polygon.Add(gp_Pnt(x, y, 0))
    polygon.Close()
    face = BRepBuilderAPI_MakeFace(polygon.Wire(), True).Face()
    return cq.Shape.cast(BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, 5)).Shape())


def validation_fixture_shapes():
    return {
        "closed_box.step": closed_box(),
        "open_shell.step": open_shell(),
        "loose_face_compound.step": loose_face_compound(),
        "touching_edge_solids.step": touching_edge_solids(),
        "touching_vertex_solids.step": touching_vertex_solids(),
        "nonmanifold_sewn_cubes.step": nonmanifold_sewn_cubes(),
        "nonmanifold_edge_prism.step": nonmanifold_edge_prism(),
        "overlapping_members.step": overlapping_members(),
        "disjoint_solids.step": disjoint_solids(),
        "bowtie_prism_invalid.brep": bowtie_prism_invalid(),
    }


def _normalize_step_header(path: Path):
    content = path.read_text()
    normalized, count = re.subn(
        r"(FILE_NAME\([^,]+,)'[^']*'",
        rf"\1'{FIXED_STEP_TIMESTAMP}'",
        content,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"Could not normalize STEP timestamp in {path}")
    normalized = "\n".join(line.rstrip() for line in normalized.splitlines()) + "\n"
    path.write_text(normalized)


def generate(output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, shape in validation_fixture_shapes().items():
        destination = output_dir / filename
        if destination.suffix == ".brep":
            BRepTools.Write_s(shape.wrapped, str(destination))
            continue
        exporters.export(shape, str(destination))
        _normalize_step_header(destination)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(
            Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "validation"
        ),
    )
    args = parser.parse_args()
    generate(args.output_dir)


if __name__ == "__main__":
    main()
