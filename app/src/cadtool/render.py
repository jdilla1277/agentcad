import math
from pathlib import Path

from OCP.AIS import AIS_InteractiveContext, AIS_Shape
from OCP.Aspect import Aspect_DisplayConnection, Aspect_NeutralWindow
from OCP.Graphic3d import (
    Graphic3d_MaterialAspect,
    Graphic3d_NameOfMaterial_Silver,
)
from OCP.Image import Image_AlienPixMap
from OCP.OpenGl import OpenGl_GraphicDriver
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.TCollection import TCollection_AsciiString
from OCP.V3d import (
    V3d_AmbientLight,
    V3d_DirectionalLight,
    V3d_TypeOfOrientation_Zup_AxoRight,
    V3d_TypeOfOrientation_Zup_Back,
    V3d_TypeOfOrientation_Zup_Bottom,
    V3d_TypeOfOrientation_Zup_Front,
    V3d_TypeOfOrientation_Zup_Left,
    V3d_TypeOfOrientation_Zup_Right,
    V3d_TypeOfOrientation_Zup_Top,
    V3d_Viewer,
)
from OCP.gp import gp_Dir

VIEWS = {
    "front": V3d_TypeOfOrientation_Zup_Front,
    "back": V3d_TypeOfOrientation_Zup_Back,
    "left": V3d_TypeOfOrientation_Zup_Left,
    "right": V3d_TypeOfOrientation_Zup_Right,
    "top": V3d_TypeOfOrientation_Zup_Top,
    "bottom": V3d_TypeOfOrientation_Zup_Bottom,
    "iso": V3d_TypeOfOrientation_Zup_AxoRight,
}

ALL_VIEWS = ["front", "right", "top", "iso"]

NAMED_VIEWS = set(VIEWS.keys())


def parse_view_spec(spec):
    """Parse a --view spec string into a list of (type, value) tuples.

    Returns:
        List of ("named", view_name) or ("custom", (azimuth, elevation)) tuples.

    Raises:
        ValueError: If the spec is invalid.
    """
    parts = [p.strip() for p in spec.split(",")]

    # Check for "all"
    if parts == ["all"]:
        return [("named", v) for v in ALL_VIEWS]

    # Check if all parts are named views
    if all(p in NAMED_VIEWS for p in parts):
        return [("named", p) for p in parts]

    # Check if exactly 2 numeric parts → custom angle
    if len(parts) == 2:
        try:
            az = float(parts[0])
            el = float(parts[1])
            return [("custom", (az, el))]
        except ValueError:
            pass

    raise ValueError(
        f"Invalid view spec '{spec}'. Use named views "
        f"({', '.join(sorted(NAMED_VIEWS))}), 'all', or 'azimuth,elevation'."
    )


def _setup_render(shape, width=800, height=600):
    """Set up offscreen rendering pipeline, returning (view, context)."""
    display_connection = Aspect_DisplayConnection()
    driver = OpenGl_GraphicDriver(display_connection)
    driver.ChangeOptions().contextNoAccel = True
    driver.ChangeOptions().buffersNoSwap = True

    viewer = V3d_Viewer(driver)
    ambient = V3d_AmbientLight(Quantity_Color(0.3, 0.3, 0.3, Quantity_TOC_RGB))
    viewer.AddLight(ambient)
    viewer.SetLightOn(ambient)
    dir_light = V3d_DirectionalLight(
        gp_Dir(1, -1, -1),
        Quantity_Color(0.8, 0.8, 0.8, Quantity_TOC_RGB),
    )
    viewer.AddLight(dir_light)
    viewer.SetLightOn(dir_light)

    view = viewer.CreateView()
    window = Aspect_NeutralWindow()
    window.SetSize(width, height)
    view.SetWindow(window)

    context = AIS_InteractiveContext(viewer)
    ais_shape = AIS_Shape(shape)
    ais_shape.SetMaterial(Graphic3d_MaterialAspect(Graphic3d_NameOfMaterial_Silver))
    context.Display(ais_shape, 1, -1, True)

    return view, context


def _capture(view, output_path, width=800, height=600):
    """Capture the current view to a PNG file."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pixmap = Image_AlienPixMap()
    view.ToPixMap(pixmap, width, height)
    pixmap.Save(TCollection_AsciiString(str(output_path)))


def _apply_camera(view, zoom, focus, fit):
    """Apply focus, fit, and zoom to a view."""
    if focus:
        view.SetAt(focus[0], focus[1], focus[2])
    if fit:
        view.FitAll()
    if zoom != 1.0:
        view.SetZoom(zoom)
    view.Redraw()


def render_shape(shape, view_name, output_path, width=800, height=600,
                 zoom=1.0, focus=None, fit=True):
    """Render a TopoDS_Shape to a PNG file from the given view."""
    orientation = VIEWS[view_name]
    view, _ctx = _setup_render(shape, width, height)

    view.SetProj(orientation)
    _apply_camera(view, zoom, focus, fit)

    _capture(view, output_path, width, height)


def render_shape_custom(shape, azimuth, elevation, output_path,
                        width=800, height=600, zoom=1.0, focus=None, fit=True):
    """Render a TopoDS_Shape to a PNG file from a custom azimuth/elevation angle."""
    az = math.radians(azimuth)
    el = math.radians(elevation)

    vx = -math.sin(az) * math.cos(el)
    vy = math.cos(az) * math.cos(el)
    vz = -math.sin(el)

    view, _ctx = _setup_render(shape, width, height)

    view.SetProj(vx, vy, vz)
    view.SetUp(0, 0, 1)
    _apply_camera(view, zoom, focus, fit)

    _capture(view, output_path, width, height)


def render_views(shape, view_names, output_dir):
    """Render multiple views of a shape, returning a dict of {view_name: path_str}."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    result = {}
    for name in view_names:
        out_path = output_dir / f"{name}.png"
        render_shape(shape, name, out_path)
        result[name] = str(out_path)
    return result
