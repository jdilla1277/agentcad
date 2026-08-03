"""Source-frame volumetric comparison for closed CAD solids."""

from dataclasses import dataclass

from OCP.BRep import BRep_Builder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS_Compound


_DEFAULT_TOLERANCE_MM = 1e-7
_SHARED_COLOR = "#d2d6dc"
_REFERENCE_ONLY_COLOR = "#0072b2"
_CANDIDATE_ONLY_COLOR = "#e69f00"


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


def _canonicalize(shape, tolerance_mm):
    """Fuse a multi-solid input into a single occupied-volume shape.

    A compound whose member solids touch or overlap EACH OTHER — e.g. the
    compound `raise_annulus` returns by design (part + boss seated on a
    face) — is a degenerate Boolean argument: Common/Cut can come back
    empty or partial, and GProp volume double-counts the overlap. Fusing
    the members first makes the comparison measure occupied volume.
    Disjoint members fuse into a valid multi-solid result, so this is safe
    for genuine assemblies too. Falls back to the original shape if the
    fuse fails, preserving the old behavior for unfusable geometry.
    """
    solids = _solids(shape)
    if len(solids) <= 1:
        return shape
    try:
        fused = solids[0]
        for solid in solids[1:]:
            fused = _run_boolean(BRepAlgoAPI_Fuse, fused, solid, tolerance_mm)
    except Exception:
        return shape
    if BRepCheck_Analyzer(fused).IsValid():
        return fused
    return shape


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


def _unavailable(tolerance_mm, code, message):
    return SolidComparison({
        **_base_response(tolerance_mm),
        "status": "unavailable",
        "reason": {
            "code": code,
            "message": message,
        },
    })


def _run_boolean(operation_type, shape_a, shape_b, tolerance_mm):
    operation = operation_type(shape_a, shape_b)
    operation.SetFuzzyValue(tolerance_mm)
    operation.Build()
    if not operation.IsDone():
        raise RuntimeError(f"{operation_type.__name__} did not complete")
    result = operation.Shape()
    if result.IsNull():
        raise RuntimeError(f"{operation_type.__name__} produced a null shape")
    return result


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

    reference_shape = _canonicalize(reference_shape, tolerance_mm)
    candidate_shape = _canonicalize(candidate_shape, tolerance_mm)

    reference_volume = _volume(reference_shape)
    candidate_volume = _volume(candidate_shape)
    if reference_volume <= 0:
        return _unavailable(
            tolerance_mm,
            "reference_has_no_positive_volume",
            "The reference has no positive solid volume.",
        )
    if candidate_volume <= 0:
        return _unavailable(
            tolerance_mm,
            "candidate_has_no_positive_volume",
            "The candidate has no positive solid volume.",
        )

    try:
        shared_shape = _run_boolean(
            BRepAlgoAPI_Common,
            reference_shape,
            candidate_shape,
            tolerance_mm,
        )
        reference_only_shape = _run_boolean(
            BRepAlgoAPI_Cut,
            reference_shape,
            candidate_shape,
            tolerance_mm,
        )
        candidate_only_shape = _run_boolean(
            BRepAlgoAPI_Cut,
            candidate_shape,
            reference_shape,
            tolerance_mm,
        )
    except Exception as exc:
        return _unavailable(
            tolerance_mm,
            "boolean_operation_failed",
            f"OpenCascade could not complete the solid comparison: {exc}",
        )

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
            )

    shared_volume = max(0.0, _volume(shared_shape))
    reference_only_volume = max(0.0, _volume(reference_only_shape))
    candidate_only_volume = max(0.0, _volume(candidate_only_shape))
    union_volume = reference_volume + candidate_volume - shared_volume

    conservation_tolerance = max(
        1e-4,
        max(reference_volume, candidate_volume) * 1e-6,
    )
    reference_error = abs(
        reference_volume - (shared_volume + reference_only_volume)
    )
    candidate_error = abs(
        candidate_volume - (shared_volume + candidate_only_volume)
    )
    if max(reference_error, candidate_error) > conservation_tolerance:
        return _unavailable(
            tolerance_mm,
            "volume_conservation_failed",
            "Boolean results did not conserve the input solid volumes.",
        )

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
