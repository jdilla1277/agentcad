"""Source-frame volumetric comparison for closed CAD solids."""

import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from OCP.BOPAlgo import BOPAlgo_CellsBuilder
from OCP.BRep import BRep_Builder
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_Copy,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
)
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopTools import TopTools_ListOfShape
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.gp import gp_Pnt


_DEFAULT_TOLERANCE_MM = 1e-7
_DEFAULT_EXACT_TIMEOUT_S = 30.0
_EXACT_TIMEOUT_ENV = "AGENTCAD_DIFF_TIMEOUT_S"
_DEFAULT_APPROXIMATE_TIMEOUT_S = 30.0
_APPROXIMATE_TIMEOUT_ENV = "AGENTCAD_APPROX_DIFF_TIMEOUT_S"
_DEFAULT_APPROXIMATE_AXIS_CELLS = 96
_DEFAULT_APPROXIMATE_MAX_CELLS = 250_000
_APPROXIMATE_RESOLUTION_ENV = "AGENTCAD_APPROX_RESOLUTION_MM"
_SHARED_COLOR = "#d2d6dc"
_REFERENCE_ONLY_COLOR = "#0072b2"
_CANDIDATE_ONLY_COLOR = "#e69f00"


class _ExactComparisonError(RuntimeError):
    """Internal failure carrying a stable result code and kernel report."""

    def __init__(self, code, message, *, kernel=None):
        super().__init__(message)
        self.code = code
        self.kernel = kernel


@dataclass
class SolidComparison:
    """JSON result plus the derived shapes used for visual artifacts."""

    data: dict
    shared_shape: object | None = None
    reference_only_shape: object | None = None
    candidate_only_shape: object | None = None

    @property
    def available(self):
        return self.data["status"] == "success"

    def colored_parts(self):
        if not self.available:
            return []

        volumes = self.data["volumes"]
        parts = []
        approximate = self.data.get("accuracy") == "approximate"
        id_prefix = "approximate_" if approximate else ""
        for part_id, name, color, shape, volume_key in (
            (
                f"{id_prefix}shared_volume",
                "Approximate shared 3D volume" if approximate else "Shared 3D volume",
                _SHARED_COLOR,
                self.shared_shape,
                "shared",
            ),
            (
                f"{id_prefix}reference_only_volume",
                (
                    "Approximate reference-only 3D volume"
                    if approximate
                    else "Reference-only 3D volume"
                ),
                _REFERENCE_ONLY_COLOR,
                self.reference_only_shape,
                "reference_only",
            ),
            (
                f"{id_prefix}candidate_only_volume",
                (
                    "Approximate candidate-only 3D volume"
                    if approximate
                    else "Candidate-only 3D volume"
                ),
                _CANDIDATE_ONLY_COLOR,
                self.candidate_only_shape,
                "candidate_only",
            ),
        ):
            if shape is not None and volumes[volume_key] > 0:
                parts.append({
                    "id": part_id,
                    "name": name,
                    "color": color,
                    "material": "matte",
                    "topo_shape": shape,
                })
        return parts

    def compound_shape(self):
        parts = self.colored_parts()
        if not parts:
            return None

        compound = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(compound)
        for part in parts:
            builder.Add(compound, part["topo_shape"])
        return compound


def _solids(shape):
    solids = []
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        solids.append(explorer.Current())
        explorer.Next()
    return solids


def _solid_count(shape):
    return len(_solids(shape))


def _shape_list(*shapes):
    result = TopTools_ListOfShape()
    for shape in shapes:
        result.Append(shape)
    return result


def _deep_copy_shape(shape):
    """Copy topology and geometry so native operations cannot alias inputs."""
    copied = BRepBuilderAPI_Copy(shape, True, False).Shape()
    if copied.IsNull():
        raise RuntimeError("OpenCascade produced a null independent copy")
    return copied


def _dump_kernel_messages(operation, method_name):
    output = BytesIO()
    getattr(operation, method_name)(output)
    messages = [
        line.strip()
        for line in output.getvalue().decode("utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    counts = {}
    for message in messages:
        counts[message] = counts.get(message, 0) + 1
    return [
        {"message": message, "count": count}
        for message, count in counts.items()
    ]


def _kernel_message_text(messages):
    return "; ".join(
        f"{item['message']} (x{item['count']})"
        for item in messages
    )


def _kernel_diagnostics(operation, label):
    """Return stable, JSON-safe OpenCascade diagnostics for one operation."""
    return {
        "operation": label,
        "non_destructive": bool(operation.NonDestructive()),
        "errors": _dump_kernel_messages(operation, "DumpErrors"),
        "warnings": _dump_kernel_messages(operation, "DumpWarnings"),
    }


def _perform_cells_partition(arguments, tolerance_mm, *, label, error_code):
    """Build one non-destructive General Fuse partition for all arguments."""
    operation = BOPAlgo_CellsBuilder()
    operation.SetNonDestructive(True)
    operation.SetFuzzyValue(tolerance_mm)
    for argument in arguments:
        operation.AddArgument(argument)
    try:
        operation.Perform()
    except Exception as exc:
        diagnostics = _kernel_diagnostics(operation, label)
        raise _ExactComparisonError(
            error_code,
            f"OpenCascade could not complete {label}: {exc}",
            kernel=diagnostics,
        ) from exc
    diagnostics = _kernel_diagnostics(operation, label)
    if operation.HasErrors():
        detail = _kernel_message_text(diagnostics["errors"]) or "unknown kernel error"
        raise _ExactComparisonError(
            error_code,
            f"OpenCascade could not complete {label}: {detail}",
            kernel=diagnostics,
        )
    return operation


def _canonicalize_occupied_volume(shape, tolerance_mm, role):
    """Partition a multi-solid input into non-overlapping occupied cells.

    General Fuse computes all self-interferences in one pass. Keeping every
    resulting cell measures physical occupied volume without double-counting
    overlapping compound members. Internal boundaries are deliberately kept:
    same-domain cleanup is not needed for comparison and can invalidate
    otherwise valid imported geometry.
    """
    solids = _solids(shape)
    if len(solids) <= 1:
        return shape, None

    label = f"{role} occupied-volume canonicalization"
    operation = _perform_cells_partition(
        solids,
        tolerance_mm,
        label=label,
        error_code=f"{role}_canonicalization_failed",
    )
    try:
        operation.RemoveAllFromResult()
        operation.AddAllToResult()
        if operation.HasErrors():
            diagnostics = _kernel_diagnostics(operation, label)
            detail = (
                _kernel_message_text(diagnostics["errors"])
                or "unknown kernel error"
            )
            raise _ExactComparisonError(
                f"{role}_canonicalization_failed",
                f"OpenCascade could not extract {label}: {detail}",
                kernel=diagnostics,
            )
        result = _deep_copy_shape(operation.Shape())
    except _ExactComparisonError:
        raise
    except Exception as exc:
        raise _ExactComparisonError(
            f"{role}_canonicalization_failed",
            f"OpenCascade could not extract {label}: {exc}",
            kernel=_kernel_diagnostics(operation, label),
        ) from exc

    diagnostics = _kernel_diagnostics(operation, label)
    if not BRepCheck_Analyzer(result).IsValid():
        raise _ExactComparisonError(
            f"{role}_canonicalization_invalid",
            f"The {role} occupied-volume partition is not a valid B-rep.",
            kernel=diagnostics,
        )
    return result, diagnostics


def _volume(shape):
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def _base_response(tolerance_mm):
    return {
        "method": "source_frame_boolean_volume",
        "alignment": {
            "mode": "source_frame",
            "transform_applied": False,
        },
        "units": {
            "length": "mm",
            "volume": "mm^3",
        },
        "tolerance": {
            "linear": tolerance_mm,
            "unit": "mm",
        },
    }


def _unavailable_suggestion(code):
    if code == "exact_comparison_timeout":
        return (
            "Retry the same saved-model `agentcad diff` with a larger "
            "AGENTCAD_DIFF_TIMEOUT_S; do not rerun the CAD build or import."
        )
    if code.endswith("_has_no_closed_solid") or code.endswith("_is_invalid"):
        return (
            "Repair or re-export the named input as valid closed solid geometry, "
            "then retry `agentcad diff`."
        )
    if code.endswith("_has_no_positive_volume"):
        return (
            "Inspect the named input and re-export it as a positive-volume closed "
            "solid before retrying `agentcad diff`."
        )
    return (
        "Review the exact failure reason and comparison_3d.kernel when present. "
        "Run an explicit `agentcad diff --visual` for the same saved models to "
        "retain the independent projection comparison; an unavailable exact result "
        "does not mean the CAD build failed."
    )


def _kernel_summary(operations, *, exact_partition_runs):
    return {
        "engine": "OpenCascade",
        "non_destructive": True,
        "exact_partition_runs": exact_partition_runs,
        "operations": operations,
    }


def _unavailable(
    tolerance_mm,
    code,
    message,
    *,
    kernel=None,
    exact_partition_runs=None,
):
    reason = {
        "code": code,
        "message": message,
    }
    data = {
        **_base_response(tolerance_mm),
        "status": "unavailable",
        "reason": reason,
        "suggestion": _unavailable_suggestion(code),
    }
    if kernel is not None or exact_partition_runs is not None:
        data["kernel"] = _kernel_summary(
            [kernel] if kernel is not None else [],
            exact_partition_runs=exact_partition_runs or 0,
        )
    return SolidComparison(data)


def _exact_timeout_seconds():
    raw = os.environ.get(_EXACT_TIMEOUT_ENV)
    if raw is None:
        return _DEFAULT_EXACT_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_EXACT_TIMEOUT_S
    if not math.isfinite(value) or value < 0:
        return _DEFAULT_EXACT_TIMEOUT_S
    return None if value == 0 else value


def _positive_env_float(name, default, *, zero_disables=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if not math.isfinite(value) or value < 0:
        return default
    if value == 0:
        return None if zero_disables else default
    return value


def _approximate_timeout_seconds():
    return _positive_env_float(
        _APPROXIMATE_TIMEOUT_ENV,
        _DEFAULT_APPROXIMATE_TIMEOUT_S,
        zero_disables=True,
    )


def _requested_approximate_resolution_mm():
    return _positive_env_float(_APPROXIMATE_RESOLUTION_ENV, None)


def _exact_worker_argv(
    reference_path,
    candidate_path,
    result_dir,
    tolerance_mm,
):
    return [
        sys.executable,
        "-m",
        "agentcad.exact_compare_worker",
        str(reference_path),
        str(candidate_path),
        str(result_dir),
        str(tolerance_mm),
    ]


def _approximate_worker_argv(
    reference_path,
    candidate_path,
    result_dir,
    resolution_mm,
):
    return [
        sys.executable,
        "-m",
        "agentcad.approximate_compare_worker",
        str(reference_path),
        str(candidate_path),
        str(result_dir),
        str(resolution_mm or 0),
    ]


def _write_brep(shape, path):
    from OCP.BRepTools import BRepTools
    from agentcad.native_io import silence_native_stdout

    with silence_native_stdout():
        written = BRepTools.Write_s(shape, str(path))
    if not written:
        raise RuntimeError(f"Could not serialize exact comparison input: {path.name}")


def _load_worker_shape(result_dir, name):
    path = result_dir / f"{name}.brep"
    if not path.exists():
        return None
    from agentcad.step_io import load_cad_shape

    return load_cad_shape(path)


def bounded_compare_solid_volumes(
    reference_shape,
    candidate_shape,
    *,
    tolerance_mm=_DEFAULT_TOLERANCE_MM,
):
    """Run exact native comparison in a worker with an enforceable deadline."""
    timeout_s = _exact_timeout_seconds()
    with tempfile.TemporaryDirectory(prefix="agentcad-exact-") as temp:
        result_dir = Path(temp)
        reference_path = result_dir / "reference.brep"
        candidate_path = result_dir / "candidate.brep"
        try:
            _write_brep(reference_shape, reference_path)
            _write_brep(candidate_shape, candidate_path)
        except Exception as exc:
            return _unavailable(
                tolerance_mm,
                "worker_input_serialization_failed",
                f"Could not prepare exact comparison worker inputs: {exc}",
            )

        try:
            completed = subprocess.run(
                _exact_worker_argv(
                    reference_path,
                    candidate_path,
                    result_dir,
                    tolerance_mm,
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            budget = timeout_s if timeout_s is not None else 0
            comparison = _unavailable(
                tolerance_mm,
                "exact_comparison_timeout",
                f"Exact 3D comparison exceeded its {budget:g}s budget.",
            )
            comparison.data["status"] = "timeout"
            comparison.data["timeout_s"] = budget
            return comparison
        except Exception as exc:
            return _unavailable(
                tolerance_mm,
                "exact_worker_launch_failed",
                f"Could not start the exact comparison worker: {exc}",
            )

        result_path = result_dir / "result.json"
        if completed.returncode != 0 or not result_path.exists():
            detail = (completed.stderr or "").strip()[-1000:]
            suffix = f": {detail}" if detail else ""
            return _unavailable(
                tolerance_mm,
                "exact_worker_failed",
                f"Exact comparison worker exited without a result{suffix}",
            )
        try:
            data = json.loads(result_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            return _unavailable(
                tolerance_mm,
                "exact_worker_result_invalid",
                f"Exact comparison worker returned an invalid result: {exc}",
            )

        if data.get("status") != "success":
            return SolidComparison(data)
        try:
            return SolidComparison(
                data,
                shared_shape=_load_worker_shape(result_dir, "shared"),
                reference_only_shape=_load_worker_shape(
                    result_dir, "reference_only"
                ),
                candidate_only_shape=_load_worker_shape(
                    result_dir, "candidate_only"
                ),
            )
        except Exception as exc:
            return _unavailable(
                tolerance_mm,
                "exact_worker_shape_invalid",
                f"Could not load exact comparison worker geometry: {exc}",
            )


def _shape_bounds(*shapes):
    bounds = Bnd_Box()
    for shape in shapes:
        BRepBndLib.Add_s(shape, bounds)
    if bounds.IsVoid():
        raise ValueError("The comparison inputs have no finite bounding box.")
    values = bounds.Get()
    if not all(math.isfinite(value) for value in values):
        raise ValueError("The comparison inputs have a non-finite bounding box.")
    return values


def _voxel_grid(reference_shape, candidate_shape, resolution_mm=None):
    x_min, y_min, z_min, x_max, y_max, z_max = _shape_bounds(
        reference_shape,
        candidate_shape,
    )
    spans = (x_max - x_min, y_max - y_min, z_max - z_min)
    longest = max(spans)
    if longest <= 0:
        raise ValueError("The comparison inputs have no positive spatial extent.")
    if resolution_mm is None:
        resolution_mm = longest / _DEFAULT_APPROXIMATE_AXIS_CELLS
    if not math.isfinite(resolution_mm) or resolution_mm <= 0:
        raise ValueError("Approximate resolution must be a positive finite number.")
    dimensions = tuple(max(1, math.ceil(span / resolution_mm)) for span in spans)
    while math.prod(dimensions) > _DEFAULT_APPROXIMATE_MAX_CELLS:
        scale = (math.prod(dimensions) / _DEFAULT_APPROXIMATE_MAX_CELLS) ** (1 / 3)
        resolution_mm *= max(scale, 1.001)
        dimensions = tuple(
            max(1, math.ceil(span / resolution_mm)) for span in spans
        )
    return (x_min, y_min, z_min), dimensions, resolution_mm


def _shape_polydata(shape, linear_deflection_mm):
    """Tessellate one solid into a VTK surface for bulk point classification."""
    import vtk
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh

    BRepMesh_IncrementalMesh(shape, linear_deflection_mm)
    points = vtk.vtkPoints()
    triangles = vtk.vtkCellArray()
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is not None:
            offset = points.GetNumberOfPoints()
            transform = location.Transformation()
            for index in range(1, triangulation.NbNodes() + 1):
                point = triangulation.Node(index).Transformed(transform)
                points.InsertNextPoint(point.X(), point.Y(), point.Z())
            reversed_face = face.Orientation() == TopAbs_REVERSED
            for index in range(1, triangulation.NbTriangles() + 1):
                node_ids = tuple(
                    node_id - 1 for node_id in triangulation.Triangle(index).Get()
                )
                if reversed_face:
                    node_ids = (node_ids[0], node_ids[2], node_ids[1])
                triangle = vtk.vtkTriangle()
                for corner, node_id in enumerate(node_ids):
                    triangle.GetPointIds().SetId(corner, offset + node_id)
                triangles.InsertNextCell(triangle)
        explorer.Next()

    surface = vtk.vtkPolyData()
    surface.SetPoints(points)
    surface.SetPolys(triangles)
    cleaner = vtk.vtkCleanPolyData()
    cleaner.SetInputData(surface)
    cleaner.Update()
    return cleaner.GetOutput()


def _grid_points(origin, dimensions, resolution_mm):
    import vtk

    points = vtk.vtkPoints()
    for z_index in range(dimensions[2]):
        z = origin[2] + (z_index + 0.5) * resolution_mm
        for y_index in range(dimensions[1]):
            y = origin[1] + (y_index + 0.5) * resolution_mm
            for x_index in range(dimensions[0]):
                points.InsertNextPoint(
                    origin[0] + (x_index + 0.5) * resolution_mm,
                    y,
                    z,
                )
    point_cloud = vtk.vtkPolyData()
    point_cloud.SetPoints(points)
    return point_cloud


def _bulk_inside_mask(shape, point_cloud, resolution_mm):
    import vtk

    inside = [False] * point_cloud.GetNumberOfPoints()
    for solid in _solids(shape):
        surface = _shape_polydata(solid, resolution_mm / 4)
        classifier = vtk.vtkSelectEnclosedPoints()
        classifier.SetInputData(point_cloud)
        classifier.SetSurfaceData(surface)
        classifier.SetTolerance(1e-7)
        classifier.Update()
        selected = (
            classifier.GetOutput().GetPointData().GetArray("SelectedPoints")
        )
        if selected is None:
            raise RuntimeError("VTK returned no enclosed-point classification.")
        for index in range(len(inside)):
            if selected.GetValue(index):
                inside[index] = True
    return inside


def _sample_voxel_occupancy(
    reference_shape,
    candidate_shape,
    origin,
    dimensions,
    resolution_mm,
):
    if not _solids(reference_shape) or not _solids(candidate_shape):
        raise ValueError("Both inputs must contain at least one closed solid.")
    point_cloud = _grid_points(origin, dimensions, resolution_mm)
    role_masks = (
        _bulk_inside_mask(reference_shape, point_cloud, resolution_mm),
        _bulk_inside_mask(candidate_shape, point_cloud, resolution_mm),
    )
    occupancy = {}
    x_cells, y_cells, _z_cells = dimensions
    for linear_index, (reference_inside, candidate_inside) in enumerate(
        zip(*role_masks)
    ):
        mask = int(reference_inside) | (int(candidate_inside) << 1)
        if not mask:
            continue
        x_index = linear_index % x_cells
        y_index = (linear_index // x_cells) % y_cells
        z_index = linear_index // (x_cells * y_cells)
        occupancy[(x_index, y_index, z_index)] = mask
    return occupancy


_VOXEL_FACES = (
    ((-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0))),
    ((1, 0, 0), ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1))),
    ((0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1))),
    ((0, 1, 0), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0))),
    ((0, 0, -1), ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0))),
    ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))),
)


def _region_voxels(occupancy, region_mask):
    return {
        index
        for index, mask in occupancy.items()
        if mask == region_mask
    }


def _voxel_surface_shape(voxels, origin, resolution_mm):
    if not voxels:
        return None
    exposed = {}
    for x_index, y_index, z_index in voxels:
        for face_index, ((dx, dy, dz), _corners) in enumerate(_VOXEL_FACES):
            if (x_index + dx, y_index + dy, z_index + dz) in voxels:
                continue
            if face_index < 2:
                plane = x_index + (face_index == 1)
                cell = (y_index, z_index)
            elif face_index < 4:
                plane = y_index + (face_index == 3)
                cell = (x_index, z_index)
            else:
                plane = z_index + (face_index == 5)
                cell = (x_index, y_index)
            exposed.setdefault((face_index, plane), set()).add(cell)

    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for (face_index, plane), cells in exposed.items():
        for u_min, v_min, u_max, v_max in _merge_grid_cells(cells):
            corners = _rectangle_corners(
                face_index,
                plane,
                u_min,
                v_min,
                u_max,
                v_max,
            )
            polygon = BRepBuilderAPI_MakePolygon()
            for x, y, z in corners:
                polygon.Add(gp_Pnt(
                    origin[0] + x * resolution_mm,
                    origin[1] + y * resolution_mm,
                    origin[2] + z * resolution_mm,
                ))
            polygon.Close()
            builder.Add(compound, BRepBuilderAPI_MakeFace(polygon.Wire()).Face())
    return compound


def _merge_grid_cells(cells):
    """Greedily merge a 2D set of unit cells into non-overlapping rectangles."""
    remaining = set(cells)
    rectangles = []
    while remaining:
        u_min, v_min = min(remaining, key=lambda item: (item[1], item[0]))
        u_max = u_min + 1
        while (u_max, v_min) in remaining:
            u_max += 1
        v_max = v_min + 1
        while all(
            (u, v_max) in remaining for u in range(u_min, u_max)
        ):
            v_max += 1
        for v in range(v_min, v_max):
            for u in range(u_min, u_max):
                remaining.remove((u, v))
        rectangles.append((u_min, v_min, u_max, v_max))
    return rectangles


def _rectangle_corners(face_index, plane, u_min, v_min, u_max, v_max):
    if face_index == 0:
        return (
            (plane, u_min, v_min),
            (plane, u_min, v_max),
            (plane, u_max, v_max),
            (plane, u_max, v_min),
        )
    if face_index == 1:
        return (
            (plane, u_min, v_min),
            (plane, u_max, v_min),
            (plane, u_max, v_max),
            (plane, u_min, v_max),
        )
    if face_index == 2:
        return (
            (u_min, plane, v_min),
            (u_max, plane, v_min),
            (u_max, plane, v_max),
            (u_min, plane, v_max),
        )
    if face_index == 3:
        return (
            (u_min, plane, v_min),
            (u_min, plane, v_max),
            (u_max, plane, v_max),
            (u_max, plane, v_min),
        )
    if face_index == 4:
        return (
            (u_min, v_min, plane),
            (u_min, v_max, plane),
            (u_max, v_max, plane),
            (u_max, v_min, plane),
        )
    return (
        (u_min, v_min, plane),
        (u_max, v_min, plane),
        (u_max, v_max, plane),
        (u_min, v_max, plane),
    )


def _boundary_voxel_count(voxels):
    return sum(
        1
        for x, y, z in voxels
        if any(
            (x + dx, y + dy, z + dz) not in voxels
            for (dx, dy, dz), _corners in _VOXEL_FACES
        )
    )


def _approximate_classification(volumes):
    if volumes["shared"] == 0:
        return "no_shared_volume"
    if volumes["reference_only"] == 0 and volumes["candidate_only"] == 0:
        return "same_occupied_volume"
    return "partial_shared_volume"


def approximate_compare_solid_volumes(
    reference_shape,
    candidate_shape,
    *,
    resolution_mm=None,
):
    """Estimate occupied-volume overlap on a bounded common voxel grid."""
    try:
        origin, dimensions, resolution_mm = _voxel_grid(
            reference_shape,
            candidate_shape,
            resolution_mm,
        )
        occupancy = _sample_voxel_occupancy(
            reference_shape,
            candidate_shape,
            origin,
            dimensions,
            resolution_mm,
        )
    except Exception as exc:
        data = {
            **_base_response(_DEFAULT_TOLERANCE_MM),
            "method": "approximate_voxel_volume",
            "status": "unavailable",
            "reason": {
                "code": "approximate_comparison_failed",
                "message": f"Could not sample approximate 3D occupancy: {exc}",
            },
        }
        return SolidComparison(data)

    region_voxels = {
        "reference_only": _region_voxels(occupancy, 1),
        "candidate_only": _region_voxels(occupancy, 2),
        "shared": _region_voxels(occupancy, 3),
    }
    reference_voxels = region_voxels["reference_only"] | region_voxels["shared"]
    candidate_voxels = region_voxels["candidate_only"] | region_voxels["shared"]
    union_voxels = set(occupancy)
    missing_roles = [
        name
        for name, voxels in (
            ("reference", reference_voxels),
            ("candidate", candidate_voxels),
        )
        if not voxels
    ]
    if missing_roles:
        return SolidComparison({
            "method": "approximate_voxel_volume",
            "status": "unavailable",
            "reason": {
                "code": "approximate_resolution_insufficient",
                "message": (
                    "The voxel grid sampled no occupied cells for the "
                    f"{' and '.join(missing_roles)} input."
                ),
            },
            "resolution_mm": round(resolution_mm, 6),
            "suggestion": (
                "Retry the saved-model diff with a smaller "
                "AGENTCAD_APPROX_RESOLUTION_MM value."
            ),
        })
    voxel_volume = resolution_mm ** 3
    volumes = {
        "reference": len(reference_voxels) * voxel_volume,
        "candidate": len(candidate_voxels) * voxel_volume,
        "shared": len(region_voxels["shared"]) * voxel_volume,
        "reference_only": len(region_voxels["reference_only"]) * voxel_volume,
        "candidate_only": len(region_voxels["candidate_only"]) * voxel_volume,
        "union": len(union_voxels) * voxel_volume,
    }
    rounded_volumes = {
        name: _round_volume(value) for name, value in volumes.items()
    }
    error_sets = {
        **region_voxels,
        "reference": reference_voxels,
        "candidate": candidate_voxels,
        "union": union_voxels,
    }
    estimated_errors = {
        name: _round_volume(_boundary_voxel_count(voxels) * voxel_volume)
        for name, voxels in error_sets.items()
    }
    reference_volume = volumes["reference"]
    candidate_volume = volumes["candidate"]
    union_volume = volumes["union"]
    volume_semantics = {
        "measurement": "sampled_voxel_occupancy",
    }
    if _solid_count(reference_shape) > 1 or _solid_count(candidate_shape) > 1:
        volume_semantics["compound_members"] = (
            "Overlapping members are counted once."
        )
    data = {
        "method": "approximate_voxel_volume",
        "accuracy": "approximate",
        "status": "success",
        "classification": _approximate_classification(volumes),
        "alignment": {
            "mode": "source_frame",
            "transform_applied": False,
        },
        "units": {"length": "mm", "volume": "mm^3"},
        "resolution_mm": round(resolution_mm, 6),
        "grid": {
            "dimensions": list(dimensions),
            "sampled_cells": math.prod(dimensions),
            "max_sampled_cells": _DEFAULT_APPROXIMATE_MAX_CELLS,
            "sampling": "voxel_center_occupancy",
            "surface_tessellation_mm": round(resolution_mm / 4, 6),
        },
        "error_estimate": {
            "method": "boundary_voxel_count",
            "unit": "mm^3",
            "absolute_volume": estimated_errors,
            "is_strict_bound": False,
            "interpretation": (
                "These are heuristic absolute errors for the named volume "
                "measurements, not additional measured volumes. Zero for an "
                "empty sampled region is not proof that sub-resolution features "
                "are absent."
            ),
        },
        "volume_semantics": volume_semantics,
        "volumes": rounded_volumes,
        "ratios": {
            "volume_iou": _round_ratio(
                volumes["shared"] / union_volume if union_volume else 0
            ),
            "reference_coverage": _round_ratio(
                volumes["shared"] / reference_volume if reference_volume else 0
            ),
            "candidate_coverage": _round_ratio(
                volumes["shared"] / candidate_volume if candidate_volume else 0
            ),
        },
        "meaning": {
            "shared": "Approximate volume occupied by both models.",
            "reference_only": "Approximate volume occupied only by the reference.",
            "candidate_only": "Approximate volume occupied only by the candidate.",
        },
        "limitations": [
            "Volumes come from voxel-center samples, not exact CAD Booleans.",
            (
                "Input surfaces are tessellated at the reported grid surface "
                "tolerance before occupancy classification."
            ),
            "The error estimate counts boundary voxels and is not a strict bound.",
            "Features thinner than the reported resolution may be missed.",
            "Requested resolution is coarsened when needed to keep the grid bounded.",
            (
                "Models were compared at the positions stored in their files; "
                "no alignment was applied."
            ),
        ],
    }
    return SolidComparison(
        data,
        shared_shape=_voxel_surface_shape(
            region_voxels["shared"], origin, resolution_mm
        ),
        reference_only_shape=_voxel_surface_shape(
            region_voxels["reference_only"], origin, resolution_mm
        ),
        candidate_only_shape=_voxel_surface_shape(
            region_voxels["candidate_only"], origin, resolution_mm
        ),
    )


def bounded_approximate_solid_volumes(
    reference_shape,
    candidate_shape,
    *,
    resolution_mm=None,
):
    """Run approximate occupancy comparison in a worker with a deadline."""
    timeout_s = _approximate_timeout_seconds()
    if resolution_mm is None:
        resolution_mm = _requested_approximate_resolution_mm()
    with tempfile.TemporaryDirectory(prefix="agentcad-approximate-") as temp:
        result_dir = Path(temp)
        reference_path = result_dir / "reference.brep"
        candidate_path = result_dir / "candidate.brep"
        try:
            _write_brep(reference_shape, reference_path)
            _write_brep(candidate_shape, candidate_path)
        except Exception as exc:
            return SolidComparison({
                "method": "approximate_voxel_volume",
                "status": "unavailable",
                "reason": {
                    "code": "approximate_worker_input_serialization_failed",
                    "message": f"Could not prepare approximate worker inputs: {exc}",
                },
            })
        try:
            completed = subprocess.run(
                _approximate_worker_argv(
                    reference_path,
                    candidate_path,
                    result_dir,
                    resolution_mm,
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            budget = timeout_s if timeout_s is not None else 0
            return SolidComparison({
                "method": "approximate_voxel_volume",
                "status": "timeout",
                "timeout_s": budget,
                "reason": {
                    "code": "approximate_comparison_timeout",
                    "message": f"Approximate 3D comparison exceeded its {budget:g}s budget.",
                },
                "suggestion": (
                    "Retry the same saved-model `agentcad diff` with a larger "
                    "AGENTCAD_APPROX_DIFF_TIMEOUT_S; do not rerun the CAD build "
                    "or import."
                ),
            })
        except Exception as exc:
            return SolidComparison({
                "method": "approximate_voxel_volume",
                "status": "unavailable",
                "reason": {
                    "code": "approximate_worker_launch_failed",
                    "message": f"Could not start the approximate comparison worker: {exc}",
                },
            })
        result_path = result_dir / "result.json"
        if completed.returncode != 0 or not result_path.exists():
            detail = (completed.stderr or "").strip()[-1000:]
            suffix = f": {detail}" if detail else ""
            return SolidComparison({
                "method": "approximate_voxel_volume",
                "status": "unavailable",
                "reason": {
                    "code": "approximate_worker_failed",
                    "message": f"Approximate worker exited without a result{suffix}",
                },
            })
        try:
            data = json.loads(result_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            return SolidComparison({
                "method": "approximate_voxel_volume",
                "status": "unavailable",
                "reason": {
                    "code": "approximate_worker_result_invalid",
                    "message": f"Approximate worker returned an invalid result: {exc}",
                },
            })
        if data.get("status") != "success":
            return SolidComparison(data)
        try:
            return SolidComparison(
                data,
                shared_shape=_load_worker_shape(result_dir, "shared"),
                reference_only_shape=_load_worker_shape(result_dir, "reference_only"),
                candidate_only_shape=_load_worker_shape(result_dir, "candidate_only"),
            )
        except Exception as exc:
            return SolidComparison({
                "method": "approximate_voxel_volume",
                "status": "unavailable",
                "reason": {
                    "code": "approximate_worker_shape_invalid",
                    "message": f"Could not load approximate worker geometry: {exc}",
                },
            })


def compare_solid_volumes_with_fallback(
    reference_shape,
    candidate_shape,
    *,
    phase_recorder=None,
):
    """Prefer exact comparison, then automatically try bounded approximation."""
    exact = None
    try:
        if phase_recorder is None:
            exact = bounded_compare_solid_volumes(reference_shape, candidate_shape)
        else:
            with phase_recorder.observe("exact_3d_comparison") as phase:
                exact = bounded_compare_solid_volumes(reference_shape, candidate_shape)
                phase.status = exact.data.get("status", "success")
                phase.message = exact.data.get("reason", {}).get("message")
    except Exception as exc:
        exact = _unavailable(
            _DEFAULT_TOLERANCE_MM,
            "exact_comparison_failed",
            f"Exact comparison raised {type(exc).__name__}: {exc}",
        )

    if exact.available:
        if phase_recorder is not None:
            phase_recorder.skip(
                "approximate_3d_comparison",
                "Exact 3D comparison succeeded; approximation was not needed.",
            )
        return exact

    approximate = None
    try:
        if phase_recorder is None:
            approximate = bounded_approximate_solid_volumes(
                reference_shape,
                candidate_shape,
            )
        else:
            with phase_recorder.observe("approximate_3d_comparison") as phase:
                approximate = bounded_approximate_solid_volumes(
                    reference_shape,
                    candidate_shape,
                )
                phase.status = approximate.data.get("status", "success")
                phase.message = approximate.data.get("reason", {}).get("message")
    except Exception as exc:
        approximate = SolidComparison({
            "method": "approximate_voxel_volume",
            "status": "unavailable",
            "reason": {
                "code": "approximate_comparison_failed",
                "message": f"Approximate comparison raised {type(exc).__name__}: {exc}",
            },
        })

    if approximate.available:
        approximate.data["exact_attempt"] = exact.data
        return approximate
    exact.data["approximate_attempt"] = approximate.data
    return exact


def _extract_partition_region(operation, take, avoid, label):
    """Extract one region without recomputing the partition/interferences."""
    try:
        operation.RemoveAllFromResult()
        operation.AddToResult(
            _shape_list(*take),
            _shape_list(*avoid),
        )
        if operation.HasErrors():
            diagnostics = _kernel_diagnostics(operation, "exact 3D partition")
            detail = (
                _kernel_message_text(diagnostics["errors"])
                or "unknown kernel error"
            )
            raise RuntimeError(detail)
        return _deep_copy_shape(operation.Shape())
    except Exception as exc:
        raise _ExactComparisonError(
            "boolean_operation_failed",
            f"OpenCascade could not extract the {label} region: {exc}",
            kernel=_kernel_diagnostics(operation, "exact 3D partition"),
        ) from exc


def _partition_regions(reference_shape, candidate_shape, tolerance_mm):
    """Compute one partition and derive shared and directional cells from it."""
    operation = _perform_cells_partition(
        [reference_shape, candidate_shape],
        tolerance_mm,
        label="exact 3D partition",
        error_code="boolean_operation_failed",
    )
    shared_shape = _extract_partition_region(
        operation,
        [reference_shape, candidate_shape],
        [],
        "shared",
    )
    reference_only_shape = _extract_partition_region(
        operation,
        [reference_shape],
        [candidate_shape],
        "reference-only",
    )
    candidate_only_shape = _extract_partition_region(
        operation,
        [candidate_shape],
        [reference_shape],
        "candidate-only",
    )
    return (
        shared_shape,
        reference_only_shape,
        candidate_only_shape,
        _kernel_diagnostics(operation, "exact 3D partition"),
    )


def _validated_region_volumes(
    reference_volume,
    candidate_volume,
    shared_shape,
    reference_only_shape,
    candidate_only_shape,
):
    """Reject impossible raw kernel values before normalization or rounding."""
    raw = {
        "shared": _volume(shared_shape),
        "reference_only": _volume(reference_only_shape),
        "candidate_only": _volume(candidate_only_shape),
    }
    all_volumes = {
        "reference": reference_volume,
        "candidate": candidate_volume,
        **raw,
    }
    if not all(math.isfinite(value) for value in all_volumes.values()):
        raise _ExactComparisonError(
            "non_finite_boolean_volume",
            "OpenCascade returned a non-finite exact comparison volume.",
        )

    conservation_tolerance = max(
        1e-4,
        max(reference_volume, candidate_volume) * 1e-6,
    )
    if any(value < -conservation_tolerance for value in raw.values()):
        raise _ExactComparisonError(
            "negative_boolean_volume",
            "OpenCascade returned a negative exact comparison region volume.",
        )
    if raw["shared"] > min(reference_volume, candidate_volume) + conservation_tolerance:
        raise _ExactComparisonError(
            "shared_volume_exceeds_input",
            "The shared region exceeds one of the input solid volumes.",
        )
    if raw["reference_only"] > reference_volume + conservation_tolerance:
        raise _ExactComparisonError(
            "subtraction_increased_volume",
            "The reference-only subtraction is larger than the reference input.",
        )
    if raw["candidate_only"] > candidate_volume + conservation_tolerance:
        raise _ExactComparisonError(
            "subtraction_increased_volume",
            "The candidate-only subtraction is larger than the candidate input.",
        )

    reference_error = abs(
        reference_volume - (raw["shared"] + raw["reference_only"])
    )
    candidate_error = abs(
        candidate_volume - (raw["shared"] + raw["candidate_only"])
    )
    if max(reference_error, candidate_error) > conservation_tolerance:
        raise _ExactComparisonError(
            "volume_conservation_failed",
            "Boolean results did not conserve the input solid volumes.",
        )

    # Tiny negative values are numerical zero. A shared value a few numerical
    # microns above an input is capped at that input. Both normalizations happen
    # only after every raw value has passed the explicit finite, sign, bound,
    # and conservation checks above.
    normalized = {
        name: max(0.0, value)
        for name, value in raw.items()
    }
    normalized["shared"] = min(
        normalized["shared"],
        reference_volume,
        candidate_volume,
    )
    return normalized


def _round_volume(value):
    rounded = round(value, 4)
    return 0.0 if rounded == 0 else rounded


def _round_ratio(value):
    rounded = round(value, 4)
    return 0.0 if rounded == 0 else rounded


def compare_solid_volumes(
    reference_shape,
    candidate_shape,
    *,
    tolerance_mm=_DEFAULT_TOLERANCE_MM,
):
    """Compare actual occupied volume without moving either input shape.

    Returns a successful ``SolidComparison`` only for valid shapes containing
    at least one closed solid. Boolean failures are represented as structured
    ``status=unavailable`` results so a normal diff can continue.
    """
    for role, shape in (
        ("reference", reference_shape),
        ("candidate", candidate_shape),
    ):
        if _solid_count(shape) == 0:
            return _unavailable(
                tolerance_mm,
                f"{role}_has_no_closed_solid",
                f"The {role} contains no closed solid volume.",
            )
        if not BRepCheck_Analyzer(shape).IsValid():
            return _unavailable(
                tolerance_mm,
                f"{role}_is_invalid",
                f"The {role} is not a valid B-rep solid.",
            )

    try:
        reference_shape = _deep_copy_shape(reference_shape)
        candidate_shape = _deep_copy_shape(candidate_shape)
    except Exception as exc:
        return _unavailable(
            tolerance_mm,
            "input_copy_failed",
            f"Could not create independent exact-comparison inputs: {exc}",
        )

    kernel_operations = []
    try:
        reference_shape, reference_diagnostics = _canonicalize_occupied_volume(
            reference_shape,
            tolerance_mm,
            "reference",
        )
        candidate_shape, candidate_diagnostics = _canonicalize_occupied_volume(
            candidate_shape,
            tolerance_mm,
            "candidate",
        )
    except _ExactComparisonError as exc:
        return _unavailable(
            tolerance_mm,
            exc.code,
            str(exc),
            kernel=exc.kernel,
            exact_partition_runs=0,
        )
    for diagnostics in (reference_diagnostics, candidate_diagnostics):
        if diagnostics is not None:
            kernel_operations.append(diagnostics)

    reference_volume = _volume(reference_shape)
    candidate_volume = _volume(candidate_shape)
    if not math.isfinite(reference_volume) or reference_volume <= 0:
        return _unavailable(
            tolerance_mm,
            "reference_has_no_positive_volume",
            "The reference has no finite positive solid volume.",
        )
    if not math.isfinite(candidate_volume) or candidate_volume <= 0:
        return _unavailable(
            tolerance_mm,
            "candidate_has_no_positive_volume",
            "The candidate has no finite positive solid volume.",
        )

    try:
        (
            shared_shape,
            reference_only_shape,
            candidate_only_shape,
            partition_diagnostics,
        ) = _partition_regions(
            reference_shape,
            candidate_shape,
            tolerance_mm,
        )
    except _ExactComparisonError as exc:
        return _unavailable(
            tolerance_mm,
            exc.code,
            str(exc),
            kernel=exc.kernel,
            exact_partition_runs=1,
        )
    except Exception as exc:
        return _unavailable(
            tolerance_mm,
            "boolean_operation_failed",
            f"OpenCascade could not complete the solid comparison: {exc}",
            exact_partition_runs=1,
        )
    kernel_operations.append(partition_diagnostics)

    for name, shape in (
        ("shared", shared_shape),
        ("reference-only", reference_only_shape),
        ("candidate-only", candidate_only_shape),
    ):
        if not BRepCheck_Analyzer(shape).IsValid():
            return _unavailable(
                tolerance_mm,
                "boolean_result_invalid",
                f"The {name} Boolean result is not a valid B-rep.",
                kernel=partition_diagnostics,
                exact_partition_runs=1,
            )

    try:
        region_volumes = _validated_region_volumes(
            reference_volume,
            candidate_volume,
            shared_shape,
            reference_only_shape,
            candidate_only_shape,
        )
    except _ExactComparisonError as exc:
        return _unavailable(
            tolerance_mm,
            exc.code,
            str(exc),
            kernel=partition_diagnostics,
            exact_partition_runs=1,
        )
    shared_volume = region_volumes["shared"]
    reference_only_volume = region_volumes["reference_only"]
    candidate_only_volume = region_volumes["candidate_only"]
    union_volume = reference_volume + candidate_volume - shared_volume

    classification_tolerance = max(
        1e-6,
        max(reference_volume, candidate_volume) * 1e-12,
    )
    if shared_volume <= classification_tolerance:
        classification = "no_shared_volume"
    elif (
        reference_only_volume <= classification_tolerance
        and candidate_only_volume <= classification_tolerance
    ):
        classification = "same_occupied_volume"
    else:
        classification = "partial_shared_volume"

    volume_semantics = {
        "measurement": "physical_occupied_volume",
    }
    if reference_diagnostics is not None or candidate_diagnostics is not None:
        volume_semantics["compound_members"] = (
            "Overlapping members are counted once here; aggregate model "
            "metrics elsewhere may sum members and double-count their overlap."
        )

    data = {
        **_base_response(tolerance_mm),
        "status": "success",
        "classification": classification,
        "kernel": _kernel_summary(
            kernel_operations,
            exact_partition_runs=1,
        ),
        "volume_semantics": volume_semantics,
        "volumes": {
            "reference": _round_volume(reference_volume),
            "candidate": _round_volume(candidate_volume),
            "shared": _round_volume(shared_volume),
            "reference_only": _round_volume(reference_only_volume),
            "candidate_only": _round_volume(candidate_only_volume),
            "union": _round_volume(union_volume),
        },
        "ratios": {
            "volume_iou": _round_ratio(shared_volume / union_volume),
            "reference_coverage": _round_ratio(
                shared_volume / reference_volume
            ),
            "candidate_coverage": _round_ratio(
                shared_volume / candidate_volume
            ),
        },
        "meaning": {
            "shared": "Physical volume occupied by both models.",
            "reference_only": (
                "Physical volume occupied only by the reference."
            ),
            "candidate_only": (
                "Physical volume occupied only by the candidate."
            ),
        },
        "limitations": [
            "Models were compared at the positions stored in their files.",
            "No translation, rotation, scaling, or automatic matching was applied.",
        ],
    }
    return SolidComparison(
        data,
        shared_shape=shared_shape,
        reference_only_shape=reference_only_shape,
        candidate_only_shape=candidate_only_shape,
    )


def write_solid_comparison_artifacts(comparison, glb_path, png_path):
    """Write a colored GLB and four-view PNG for a successful comparison."""
    if not comparison.available:
        return False

    shape = comparison.compound_shape()
    parts = comparison.colored_parts()
    if shape is None or not parts:
        return False

    from agentcad.export import export_glb
    from agentcad.render import render_solid_comparison

    export_glb(shape, glb_path, parts=parts)
    render_solid_comparison(
        shape,
        parts,
        png_path,
        comparison_data=comparison.data,
    )
    return True
