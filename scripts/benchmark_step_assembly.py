#!/usr/bin/env python3
"""Measure STEP structure/membership intake; never claims full-report latency.

Run with PYTHONPATH=src python scripts/benchmark_step_assembly.py [files...].
Default inputs are the two real workflow fixtures plus generated assemblies.
Output is JSON; native writer diagnostics stay off stdout.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import statistics
import sys
import tempfile
from pathlib import Path
from time import perf_counter

from agentcad.step_assembly import read_step_assembly
from agentcad.topo_ids import face_entries, edge_entries, pick_face_topods, topology_counts


def _generated_fixture(path: Path, *, repeated: bool, count: int | None = None) -> Path:
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS_Compound
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.gp import gp_Pnt, gp_Trsf, gp_Vec

    from agentcad.native_io import silence_native_stdout

    document = TDocStd_Document(TCollection_ExtendedString("XCAF"))
    tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    if repeated:
        root = tool.AddShape(compound, True)
        leaf = tool.AddShape(BRepPrimAPI_MakeBox(1, 2, 3).Shape(), False)
        for i in range(100 if count is None else count):
            transform = gp_Trsf()
            transform.SetTranslation(gp_Vec(4 * i, 0, 0))
            tool.AddComponent(root, leaf, TopLoc_Location(transform))
        tool.UpdateAssemblies()
    else:
        # A single source part can contain many solids. Its 900 faces stress
        # membership and measurement work without requiring a slow Boolean.
        for i in range(150 if count is None else count):
            builder.Add(compound, BRepPrimAPI_MakeBox(gp_Pnt(4 * i, 0, 0), 1, 2, 3).Shape())
        root = tool.AddShape(compound, False)
    TDataStd_Name.Set_s(root, TCollection_ExtendedString(path.stem))
    with silence_native_stdout():
        writer = STEPCAFControl_Writer()
        writer.SetNameMode(True)
        if not writer.Transfer(document, STEPControl_AsIs):
            raise RuntimeError("generated fixture transfer failed")
        if writer.Write(str(path)) != IFSelect_RetDone:
            raise RuntimeError("generated fixture write failed")
    return path


def _measure(path: Path, repeats: int) -> dict:
    read_times = []
    inventory_times = []
    for _ in range(repeats + 1):
        start = perf_counter()
        document = read_step_assembly(path)
        read_times.append(perf_counter() - start)
        start = perf_counter()
        faces = face_entries(document.shape)
        edge_entries(document.shape)
        inventory_times.append(perf_counter() - start)
    lookup_times = []
    if faces:
        for _ in range(20):
            start = perf_counter()
            pick_face_topods(document.shape, faces[-1]["id"])
            lookup_times.append(perf_counter() - start)
    return {
        "fixture": path.name,
        "bytes": path.stat().st_size,
        "counts": topology_counts(document.shape),
        "part_definitions": sum(d.kind == "part" for d in document.definitions),
        "leaf_occurrences": sum(o.mapping_status != "assembly" for o in document.occurrences),
        "first_read_s": read_times[0],
        "warm_read_median_s": statistics.median(read_times[1:]),
        "warm_inventory_median_s": statistics.median(inventory_times[1:]),
        "warm_face_lookup_median_s": statistics.median(lookup_times) if lookup_times else None,
        "samples": repeats,
        "all_leaf_memberships_exact": all(
            o.mapping_status in {"source_exact", "assembly"} for o in document.occurrences
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=Path, nargs="*")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    with tempfile.TemporaryDirectory(prefix="agentcad-step-intake-") as temp:
        paths = args.files
        if not paths:
            fixtures = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "real_world"
            paths = [
                fixtures / "cadgenbench_101_agentcad.step",
                fixtures / "pump_manifold.step",
                _generated_fixture(Path(temp) / "repeated_100.step", repeated=True),
                _generated_fixture(Path(temp) / "high_face_900.step", repeated=False),
            ]
        measurements = [_measure(path, args.repeats) for path in paths]
    peak_rss = None
    try:
        import resource

        native_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_rss = native_rss if sys.platform == "darwin" else native_rss * 1024
    except ImportError:
        pass
    print(json.dumps({
        "scope": "STEP parse, source hierarchy/membership, face/edge measurements, numeric lookup",
        "excluded": ["validation", "recognition", "renders", "artifact delivery", "remote transfer"],
        "timing_note": "First read is process-local, not a cold filesystem guarantee; warm medians exclude first read.",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "versions": {p: importlib.metadata.version(p) for p in ("build123d", "cadquery-ocp")},
        "peak_process_rss_bytes": peak_rss,
        "measurements": measurements,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
