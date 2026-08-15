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
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopTools import TopTools_ListOfShape
from OCP.TopoDS import TopoDS_Compound


_DEFAULT_TOLERANCE_MM = 1e-7
_DEFAULT_EXACT_TIMEOUT_S = 30.0
_EXACT_TIMEOUT_ENV = "AGENTCAD_DIFF_TIMEOUT_S"
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
        for part_id, name, color, shape, volume_key in (
            (
                "shared_volume",
                "Shared 3D volume",
                _SHARED_COLOR,
                self.shared_shape,
                "shared",
            ),
            (
                "reference_only_volume",
                "Reference-only 3D volume",
                _REFERENCE_ONLY_COLOR,
                self.reference_only_shape,
                "reference_only",
            ),
            (
                "candidate_only_volume",
                "Candidate-only 3D volume",
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
    return [
        line.strip()
        for line in output.getvalue().decode("utf-8", errors="replace").splitlines()
        if line.strip()
    ]


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
        detail = "; ".join(diagnostics["errors"]) or "unknown kernel error"
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
            detail = "; ".join(diagnostics["errors"]) or "unknown kernel error"
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
            "Retry the saved models with `agentcad diff REF1 REF2` and a larger "
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
        "Review the exact failure reason and reason.kernel when present. Run "
        "`agentcad diff REF1 REF2 --visual` to retain the independent projection "
        "comparison; an unavailable exact result does not mean the CAD build failed."
    )


def _unavailable(tolerance_mm, code, message, *, kernel=None):
    reason = {
        "code": code,
        "message": message,
    }
    if kernel is not None:
        reason["kernel"] = kernel
    return SolidComparison({
        **_base_response(tolerance_mm),
        "status": "unavailable",
        "reason": reason,
        "suggestion": _unavailable_suggestion(code),
    })


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
            detail = "; ".join(diagnostics["errors"]) or "unknown kernel error"
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
        )
    except Exception as exc:
        return _unavailable(
            tolerance_mm,
            "boolean_operation_failed",
            f"OpenCascade could not complete the solid comparison: {exc}",
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

    data = {
        **_base_response(tolerance_mm),
        "status": "success",
        "classification": classification,
        "kernel": {
            "engine": "OpenCascade",
            "non_destructive": True,
            "partition_passes": 1,
            "operations": kernel_operations,
        },
        "volume_semantics": {
            "measurement": "physical_occupied_volume",
            "compound_members": (
                "Overlapping members are counted once here; aggregate model "
                "metrics elsewhere may sum members and double-count their overlap."
            ),
        },
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
