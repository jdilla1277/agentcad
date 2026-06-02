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

    # "all" shorthand
    if parts == ["all"]:
        return [("named", v) for v in ALL_VIEWS]

    # All named views (fast path, backward compat)
    if all(p in NAMED_VIEWS for p in parts):
        return [("named", p) for p in parts]

    # Check for colon-separated angles (mixed mode)
    if any(":" in p for p in parts):
        result = []
        for p in parts:
            if p in NAMED_VIEWS:
                result.append(("named", p))
            elif ":" in p:
                az_s, el_s = p.split(":", 1)
                try:
                    result.append(("custom", (float(az_s), float(el_s))))
                except ValueError:
                    raise ValueError(f"Invalid angle spec '{p}'. Use 'azimuth:elevation'.")
            else:
                raise ValueError(
                    f"Invalid view '{p}' in spec '{spec}'. "
                    f"Named views: {', '.join(sorted(NAMED_VIEWS))}. "
                    f"Custom angles: 'azimuth:elevation'."
                )
        return result

    # Legacy: exactly 2 numeric parts → single custom angle
    if len(parts) == 2:
        try:
            return [("custom", (float(parts[0]), float(parts[1])))]
        except ValueError:
            pass

    raise ValueError(
        f"Invalid view spec '{spec}'. Use named views "
        f"({', '.join(sorted(NAMED_VIEWS))}), 'all', or 'azimuth:elevation'."
    )


def _setup_render(shape, width=800, height=600):
    """Set up offscreen rendering pipeline, returning (view, context)."""
    display_connection = Aspect_DisplayConnection()
    driver = OpenGl_GraphicDriver(display_connection)
    driver.ChangeOptions().contextNoAccel = True
    driver.ChangeOptions().buffersNoSwap = True

    viewer = V3d_Viewer(driver)
    ambient = V3d_AmbientLight(Quantity_Color(0.5, 0.5, 0.5, Quantity_TOC_RGB))
    viewer.AddLight(ambient)
    viewer.SetLightOn(ambient)
    key_light = V3d_DirectionalLight(
        gp_Dir(1, -1, -1),
        Quantity_Color(0.8, 0.8, 0.8, Quantity_TOC_RGB),
    )
    viewer.AddLight(key_light)
    viewer.SetLightOn(key_light)
    fill_light = V3d_DirectionalLight(
        gp_Dir(-1, 1, 0.5),
        Quantity_Color(0.4, 0.4, 0.4, Quantity_TOC_RGB),
    )
    viewer.AddLight(fill_light)
    viewer.SetLightOn(fill_light)

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


def render_shape_batch(shape, view_specs, output_paths, width=512, height=512):
    """Render multiple views of the same shape through ONE viewer setup.

    Shares the OpenGL context, lights, and AIS geometry upload across all views —
    much faster than N sequential render_shape() calls that each build fresh
    setup from scratch.

    Args:
        shape: TopoDS_Shape to render.
        view_specs: Iterable of either named views ("front", "iso", ...) or
            custom (azimuth, elevation) tuples in degrees.
        output_paths: Parallel iterable of output PNG paths.
        width, height: Per-view dimensions in pixels.
    """
    view, _ctx = _setup_render(shape, width, height)
    for spec, out_path in zip(view_specs, output_paths):
        if isinstance(spec, str):
            view.SetProj(VIEWS[spec])
        else:
            az, el = spec
            az_r = math.radians(az)
            el_r = math.radians(el)
            vx = -math.sin(az_r) * math.cos(el_r)
            vy = math.cos(az_r) * math.cos(el_r)
            vz = -math.sin(el_r)
            view.SetProj(vx, vy, vz)
            view.SetUp(0, 0, 1)
        view.FitAll()
        view.Redraw()
        _capture(view, out_path, width, height)


# The default composite is one top-down layout view + three iso angles spaced
# around the part, so any asymmetric feature (arm, clamp, bracket, etc.) is
# visible from at least one angle. Chosen over the classic orthographic
# front/right/top/iso set because, for agents, dimensions come from the
# metrics JSON — images need to maximize geometry coverage, not ruler-accuracy.
_COMPOSITE_VIEWS = [
    ("TOP", "top"),
    ("ISO FRONT-RIGHT", (45, 25)),
    ("ISO BACK-RIGHT", (-45, 25)),
    ("ISO BACK-LEFT", (-135, 25)),
]


def render_composite_4view(shape, output_path, per_view_size=512):
    """Render a 4-panel composite: top view + three iso angles spaced around the part.

    This is the default preview agents get after every successful run. One
    shape → four informative angles → single image.
    """
    import tempfile
    from PIL import Image, ImageDraw

    labels = [v[0] for v in _COMPOSITE_VIEWS]
    specs = [v[1] for v in _COMPOSITE_VIEWS]

    with tempfile.TemporaryDirectory() as tmp:
        tmp_paths = [Path(tmp) / f"panel_{i}.png" for i in range(len(specs))]
        render_shape_batch(shape, specs, tmp_paths,
                           width=per_view_size, height=per_view_size)

        imgs = [Image.open(p).convert("RGB") for p in tmp_paths]
        w, h = per_view_size, per_view_size
        label_h = 22
        composite = Image.new("RGB", (w * 2, (h + label_h) * 2), (245, 245, 245))

        # 2x2 grid in row-major order
        positions = [(0, 0), (w, 0), (0, h + label_h), (w, h + label_h)]
        for img, (x, y) in zip(imgs, positions):
            composite.paste(img, (x, y + label_h))

        draw = ImageDraw.Draw(composite)
        for (x, y), label in zip(positions, labels):
            draw.text((x + 10, y + 4), label, fill=(40, 40, 40))
        # Dividers
        draw.line([(w, 0), (w, (h + label_h) * 2)], fill=(200, 200, 200), width=1)
        draw.line([(0, h + label_h), (w * 2, h + label_h)], fill=(200, 200, 200), width=1)

        composite.save(str(output_path))


def render_diff_overlay(shape_a, shape_b, label_a, label_b, output_path,
                        width=1024, height=1024, view_name="iso"):
    """Render a tinted overlay of two shapes: A in green, B in red, alpha-blended.

    Each shape is rendered independently (necessary — OCP V3d_View can't render
    two shapes with different materials in one pass reliably), then PIL
    composites them with alpha blending. Depth ordering is approximate — when
    shapes partially occlude each other, the overlay is visually approximate
    but usually still readable.
    """
    import tempfile
    from PIL import Image, ImageChops, ImageDraw

    with tempfile.TemporaryDirectory() as tmp:
        a_path = Path(tmp) / "a.png"
        b_path = Path(tmp) / "b.png"
        # Same viewer setup for both since shapes differ — accept the cost
        render_shape(shape_a, view_name, a_path, width=width, height=height)
        render_shape(shape_b, view_name, b_path, width=width, height=height)

        a_img = Image.open(a_path).convert("RGB")
        b_img = Image.open(b_path).convert("RGB")

        # Tint each: A green, B red. Multiply-blend gray renders with tint.
        def tint(img, tint_color):
            tint_layer = Image.new("RGB", img.size, tint_color)
            return ImageChops.multiply(img, tint_layer)

        a_tinted = tint(a_img, (180, 255, 180))   # greenish
        b_tinted = tint(b_img, (255, 180, 180))   # reddish

        # Blend 50/50
        composite = Image.blend(a_tinted, b_tinted, 0.5)

        # Legend
        draw = ImageDraw.Draw(composite)
        draw.rectangle([(10, 10), (26, 26)], fill=(120, 200, 120))
        draw.text((32, 10), f"A (prev) · {label_a}", fill=(40, 40, 40))
        draw.rectangle([(10, 32), (26, 48)], fill=(220, 120, 120))
        draw.text((32, 32), f"B (this) · {label_b}", fill=(40, 40, 40))

        composite.save(str(output_path))


def render_diff_side_by_side(shape_a, shape_b, label_a, label_b, output_path,
                             width=512, height=512, view_name="iso"):
    """Render two shapes side-by-side as a single comparison PNG.

    Renders each shape independently from the same view, then composites them
    horizontally with labels above each. Designed for agents that need a single
    image to reason about what changed between two versions.
    """
    import tempfile
    from PIL import Image, ImageDraw

    label_h = 28  # pixels reserved above each render for the label bar

    with tempfile.TemporaryDirectory() as tmp:
        a_path = Path(tmp) / "a.png"
        b_path = Path(tmp) / "b.png"
        render_shape(shape_a, view_name, a_path, width=width, height=height)
        render_shape(shape_b, view_name, b_path, width=width, height=height)

        a_img = Image.open(a_path).convert("RGB")
        b_img = Image.open(b_path).convert("RGB")

        composite_w = a_img.width + b_img.width
        composite_h = max(a_img.height, b_img.height) + label_h
        composite = Image.new("RGB", (composite_w, composite_h), (245, 245, 245))
        composite.paste(a_img, (0, label_h))
        composite.paste(b_img, (a_img.width, label_h))

        draw = ImageDraw.Draw(composite)
        # Labels — PIL's default font is small but always available
        draw.text((12, 6), f"A · {label_a}", fill=(40, 40, 40))
        draw.text((a_img.width + 12, 6), f"B · {label_b}", fill=(40, 40, 40))
        # Thin divider between the two renders
        divider_x = a_img.width
        draw.line([(divider_x, 0), (divider_x, composite_h)], fill=(180, 180, 180), width=1)

        composite.save(str(output_path))


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
