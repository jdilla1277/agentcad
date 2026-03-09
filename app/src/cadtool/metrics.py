"""Geometric metrics computed from a TopoDS_Shape."""

from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape


def compute_metrics(topo_shape):
    """Compute geometric metrics from a TopoDS_Shape.

    Returns a JSON-serializable dict with bounding_box, dimensions, volume,
    surface_area, center_of_mass, face_count, edge_count, and is_valid.
    """
    # Bounding box
    bbox = Bnd_Box()
    BRepBndLib.Add_s(topo_shape, bbox)
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

    return {
        "bounding_box": bb,
        "dimensions": dims,
        "volume": volume,
        "surface_area": surface_area,
        "center_of_mass": center_of_mass,
        "face_count": face_count,
        "edge_count": edge_count,
        "is_valid": is_valid,
    }
