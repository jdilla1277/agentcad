"""Geometric metrics computed from a TopoDS_Shape."""

from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepTools import BRepTools
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer as TopExp_Exp
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape


def extract_validity_errors(analyzer, topo_shape):
    """Return a sorted list of distinct BRepCheck error class names for an
    invalid shape (e.g. 'BRepCheck_SelfIntersectingWire'). Empty if none."""
    errors = set()
    for shape_type in (TopAbs_FACE, TopAbs_EDGE, TopAbs_WIRE, TopAbs_SHELL):
        exp = TopExp_Exp(topo_shape, shape_type)
        while exp.More():
            check_result = analyzer.Result(exp.Current())
            if check_result:
                for status in check_result.Status():
                    if status.value != 0:  # BRepCheck_NoError = 0
                        errors.add(status.name)
            exp.Next()
    return sorted(errors)


def compute_metrics(topo_shape):
    """Compute geometric metrics from a TopoDS_Shape.

    Returns a JSON-serializable dict with bounding_box, dimensions, volume,
    surface_area, center_of_mass, face_count, edge_count, and is_valid.
    """
    # Bounding box.
    #
    # Use AddOptimal_s, not Add_s. Add_s derives each face/edge bound from the
    # *poles* (control points) of its B-spline/NURBS geometry, which for
    # rational curves and swept/lofted surfaces lie well outside the actual
    # trimmed geometry. STEP exporters routinely emit circles, cylinders, and
    # blade surfaces as NURBS, so Add_s can inflate `dimensions` far past the
    # real envelope (CADGenBench 203 impeller: 586.7 reported vs 360 real).
    # AddOptimal_s computes a tight box from the trimmed geometry and matches
    # build123d's `bounding_box()` (which is what produced the correct 360
    # envelope in that benchmark). Clean_s first to drop any cached
    # triangulation that AddOptimal would otherwise prefer over the exact
    # geometry — same sequence build123d uses.
    BRepTools.Clean_s(topo_shape)
    bbox = Bnd_Box()
    BRepBndLib.AddOptimal_s(topo_shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()

    bb = {
        "x": [round(xmin, 4), round(xmax, 4)],
        "y": [round(ymin, 4), round(ymax, 4)],
        "z": [round(zmin, 4), round(zmax, 4)],
    }

    dims = {
        "x": round(xmax - xmin, 4),
        "y": round(ymax - ymin, 4),
        "z": round(zmax - zmin, 4),
    }

    # Volume and center of mass
    vol_props = GProp_GProps()
    BRepGProp.VolumeProperties_s(topo_shape, vol_props)
    volume = round(vol_props.Mass(), 4)

    com = vol_props.CentreOfMass()
    center_of_mass = {
        "x": round(com.X(), 4),
        "y": round(com.Y(), 4),
        "z": round(com.Z(), 4),
    }

    # Surface area
    surf_props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(topo_shape, surf_props)
    surface_area = round(surf_props.Mass(), 4)

    # Face count (unique)
    face_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(topo_shape, TopAbs_FACE, face_map)
    face_count = face_map.Extent()

    # Edge count (unique)
    edge_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(topo_shape, TopAbs_EDGE, edge_map)
    edge_count = edge_map.Extent()

    # Validity
    analyzer = BRepCheck_Analyzer(topo_shape)
    is_valid = analyzer.IsValid()

    result = {
        "bounding_box": bb,
        "dimensions": dims,
        "volume": volume,
        "surface_area": surface_area,
        "center_of_mass": center_of_mass,
        "face_count": face_count,
        "edge_count": edge_count,
        "is_valid": is_valid,
    }

    # Validity diagnostics — when invalid, extract BRepCheck error descriptions
    if not is_valid:
        errors = extract_validity_errors(analyzer, topo_shape)
        if errors:
            result["validity_errors"] = errors

    # Negative volume warning
    warnings = []
    if volume < 0:
        warnings.append(
            "Negative volume detected — shape may have inverted normals. "
            "If you built a wire manually, check winding order (CCW in XY → +Z normal). "
            "Try reversing the wire or using subtractive construction (cut from a blank)."
        )
    if warnings:
        result["warnings"] = warnings

    return result
