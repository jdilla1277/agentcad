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


def render_shape(shape, view_name, output_path, width=800, height=600):
    """Render a TopoDS_Shape to a PNG file from the given view."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    orientation = VIEWS[view_name]

    # Set up offscreen OpenGL driver (software renderer)
    display_connection = Aspect_DisplayConnection()
    driver = OpenGl_GraphicDriver(display_connection)
    driver.ChangeOptions().contextNoAccel = True
    driver.ChangeOptions().buffersNoSwap = True

    # Create viewer with lights
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

    # Create view with offscreen window
    view = viewer.CreateView()
    window = Aspect_NeutralWindow()
    window.SetSize(width, height)
    view.SetWindow(window)

    # Display shape in AIS context
    context = AIS_InteractiveContext(viewer)
    ais_shape = AIS_Shape(shape)
    ais_shape.SetMaterial(Graphic3d_MaterialAspect(Graphic3d_NameOfMaterial_Silver))
    context.Display(ais_shape, True)

    # Set orientation, fit, and render
    view.SetProj(orientation)
    view.FitAll()
    view.Redraw()

    # Capture to pixmap and save
    pixmap = Image_AlienPixMap()
    view.ToPixMap(pixmap, width, height)
    pixmap.Save(TCollection_AsciiString(str(output_path)))


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
