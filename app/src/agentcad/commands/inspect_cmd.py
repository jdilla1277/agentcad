import json
import sys
from pathlib import Path

import click


@click.command("inspect")
@click.argument("file")
def inspect_cmd(file):
    """Inspect a STEP file's topology — shells, faces, edges, orientations."""
    file_path = Path(file).resolve()

    if not file_path.exists():
        click.echo(json.dumps({
            "command": "inspect",
            "status": "error",
            "message": f"File '{file}' not found",
        }))
        sys.exit(1)

    from cadquery import importers
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.ShapeAnalysis import ShapeAnalysis_Shell
    from OCP.TopAbs import (
        TopAbs_EDGE, TopAbs_FACE, TopAbs_FORWARD, TopAbs_REVERSED,
        TopAbs_SHELL, TopAbs_SOLID,
    )
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS

    shape = importers.importStep(str(file_path)).val().wrapped

    # Solid count
    solid_count = 0
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        solid_count += 1
        exp.Next()

    # Shell analysis
    shells = []
    exp = TopExp_Explorer(shape, TopAbs_SHELL)
    while exp.More():
        shell = TopoDS.Shell_s(exp.Current())
        sa = ShapeAnalysis_Shell()
        sa.LoadShells(shell)
        has_free = sa.HasFreeEdges()

        # Count faces in this shell
        shell_face_count = 0
        face_exp = TopExp_Explorer(shell, TopAbs_FACE)
        while face_exp.More():
            shell_face_count += 1
            face_exp.Next()

        shells.append({
            "closed": not has_free,
            "face_count": shell_face_count,
        })
        exp.Next()

    # Face count and orientations
    face_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, face_map)
    face_count = face_map.Extent()

    forward = reversed_count = 0
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        orient = exp.Current().Orientation()
        if orient == TopAbs_FORWARD:
            forward += 1
        elif orient == TopAbs_REVERSED:
            reversed_count += 1
        exp.Next()

    # Edge count
    edge_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edge_map)
    edge_count = edge_map.Extent()

    # Free edges (edges belonging to only one face)
    free_edge_count = 0
    for i in range(1, edge_count + 1):
        edge = edge_map.FindKey(i)
        # Count how many faces reference this edge
        face_ref_count = 0
        face_exp = TopExp_Explorer(shape, TopAbs_FACE)
        while face_exp.More():
            edge_exp = TopExp_Explorer(face_exp.Current(), TopAbs_EDGE)
            while edge_exp.More():
                if edge_exp.Current().IsSame(edge):
                    face_ref_count += 1
                    break
                edge_exp.Next()
            face_exp.Next()
        if face_ref_count < 2:
            free_edge_count += 1

    # Validity
    analyzer = BRepCheck_Analyzer(shape)
    is_valid = analyzer.IsValid()

    click.echo(json.dumps({
        "command": "inspect",
        "status": "success",
        "file": str(file_path),
        "solid_count": solid_count,
        "shell_count": len(shells),
        "shells": shells,
        "face_count": face_count,
        "face_orientations": {
            "forward": forward,
            "reversed": reversed_count,
        },
        "edge_count": edge_count,
        "free_edge_count": free_edge_count,
        "is_valid": is_valid,
    }))
