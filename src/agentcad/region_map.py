"""Labeled region map PNG for ``show_regions``.

The map is a flat, orthographic picture of the sketch plane (looking along
-z_dir with the plane's y_dir up). It is drawn directly in 2D: each region's
triangulation is rasterized into an integer ID image, which gives both the
colored fill and, after shrinking away the outlines, the exact pixels where
a region's index label can sit (the same fit logic ``--highlight-faces``
uses). Every index must stay readable, so a region too small for its label
gets the label outside the drawing with a thin leader line into the region.
"""

from __future__ import annotations

import math

from agentcad import face_labels

INSIDE = "inside"
LEADER = "leader"
OUTSIDE_WINDOW = "outside_window"

_SUPERSAMPLE = 2
_FILL_TINT = 0.5  # share of white mixed into palette colors for region fills
_OUTLINE_RGB = (35, 35, 35)
_LEADER_RGB = (25, 25, 25)
_BACKGROUND_RGB = (255, 255, 255)
_GRID_RGB = (222, 226, 234)
_FRAME_RGB = (120, 120, 120)
_AXIS_RGB = (90, 90, 90)
_AXIS_TEXT_RGB = (40, 40, 40)
# Fewer visible pixels than this (or at most 2 px thick) flags a region tiny.
_TINY_PIXELS = 16
# Label font sizes tried, as fractions of the image's long side.
_FONT_FRACTIONS = (0.02, 0.0165, 0.0135)


def _tint(rgb, amount=_FILL_TINT):
    return tuple(round(c * (1 - amount) + 255 * amount) for c in rgb)


def _image_size(local_bounds, count):
    """(scale px/unit, width, height, margin) for the drawing."""
    xmin, ymin, xmax, ymax = local_bounds
    span_x = max(xmax - xmin, 1e-9)
    span_y = max(ymax - ymin, 1e-9)
    # Bigger canvas for many regions, capped so the PNG stays readable when
    # an agent's image viewer downsizes it.
    long_side = int(min(1400, max(900, 160 * count ** 0.5)))
    margin = max(70, round(long_side * 0.12))
    scale = long_side / max(span_x, span_y)
    # Keep thin sketches from producing a sliver of an image.
    draw_w = max(span_x * scale, long_side * 0.35)
    draw_h = max(span_y * scale, long_side * 0.35)
    width = int(round(draw_w + 2 * margin))
    height = int(round(draw_h + 2 * margin))
    return scale, width, height, margin, draw_w, draw_h


def _adjacency(ids, count):
    import numpy as np

    pairs = set()
    for a, b in (
        (ids[:, :-1], ids[:, 1:]),
        (ids[:-1, :], ids[1:, :]),
        (ids[:, :-2], ids[:, 2:]),
        (ids[:-2, :], ids[2:, :]),
    ):
        touching = (a != b) & (a > 0) & (b > 0)
        if touching.any():
            stacked = np.unique(np.stack([a[touching], b[touching]], axis=1), axis=0)
            pairs.update((int(x), int(y)) for x, y in stacked)
    neighbors = {index: set() for index in range(1, count + 1)}
    for x, y in pairs:
        neighbors[x].add(y)
        neighbors[y].add(x)
    return neighbors


def _assign_colors(neighbors, count):
    """Greedy palette coloring so touching regions differ in color."""
    palette_size = len(face_labels.HIGHLIGHT_PALETTE)
    colors = {}
    for index in range(1, count + 1):
        used = {colors[n] for n in neighbors[index] if n in colors}
        free = [c for c in range(palette_size) if c not in used]
        colors[index] = free[0] if free else (index - 1) % palette_size
    return [_tint(face_labels.HIGHLIGHT_PALETTE[colors[i]]) for i in range(1, count + 1)]


def _erode(mask, pixels):
    import numpy as np

    out = mask.copy()
    for _ in range(pixels):
        shrunk = out.copy()
        shrunk[1:, :] &= out[:-1, :]
        shrunk[:-1, :] &= out[1:, :]
        shrunk[:, 1:] &= out[:, :-1]
        shrunk[:, :-1] &= out[:, 1:]
        shrunk[0, :] = shrunk[-1, :] = False
        shrunk[:, 0] = shrunk[:, -1] = False
        out = shrunk
    return out


def _dilate(mask, pixels):
    out = mask.copy()
    for _ in range(pixels):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def _plane_name(plane):
    from build123d import Plane

    for name in ("XY", "XZ", "YZ"):
        named = getattr(Plane, name)
        if (named.z_dir - plane.z_dir).length < 1e-9 and (named.x_dir - plane.x_dir).length < 1e-9:
            return name
    return None


def _nice_step(span, target=8):
    """A 1/2/2.5/5 x 10^k tick step giving about ``target`` ticks."""
    raw = max(span, 1e-12) / target
    magnitude = 10 ** math.floor(math.log10(raw))
    for multiple in (1, 2, 2.5, 5, 10):
        if multiple * magnitude >= raw * (1 - 1e-9):
            return multiple * magnitude
    return 10 * magnitude


def _tick_values(low, high, step):
    first = math.ceil(low / step - 1e-9)
    last = math.floor(high / step + 1e-9)
    return [k * step for k in range(first, last + 1)]


def _tick_text(value, step):
    exponent = math.floor(math.log10(step) + 1e-9)
    decimals = max(0, -exponent)
    if abs(step / 10 ** exponent - 2.5) < 1e-9:
        decimals += 1
    text = f"{value:.{decimals}f}"
    return "0" if float(text) == 0 else text


def _is_tiny(mask):
    """A region a reader cannot make out: a few pixels, or at most 2 px thick."""
    return int(mask.sum()) < _TINY_PIXELS or not _erode(mask, 1).any()


def render_region_map(faces, plane, output_path, window=None, interiors=None):
    """Draw ``faces`` (index = list position) as a labeled region map PNG.

    ``window`` is an optional plane-local ``(xmin, ymin, xmax, ymax)``: only
    that area is drawn and regions outside it are not labeled. Axis ticks
    along the bottom and left give plane-local coordinates.

    ``interiors`` are the plane-local interior points reported in the JSON
    (computed when omitted). A label sits centered on its region's interior
    point whenever it fits there, else at the nearest spot in the region
    where it fits; a leader line points at the interior point. So the map
    and ``items[].interior`` always agree.

    Returns one dict per face, in order: ``label`` (inside / leader /
    outside_window), ``color`` (the fill's hex color on the map), ``tiny``
    (only a few pixels, or at most 2 px thick, on this map) and, for
    leaders, ``clear`` (False when the label had to overlap something).
    For checks, drawn labels also carry ``box`` (x, y, w, h), ``target``
    (the interior point, or None when it is off the map) and, for leaders,
    ``anchor``, all in map pixels left of the axis band.
    """
    import numpy as np
    from PIL import Image, ImageDraw

    from agentcad.sketch_regions import edge_points, interior_point

    count = len(faces)
    if interiors is None:
        interiors = [interior_point(face, plane) for face in faces]

    def to_local(point):
        local = plane.to_local_coords(point)
        return local.X, local.Y

    # Sample once in plane-local coordinates, fine enough for ~0.3 px at
    # the largest canvas, then fit the canvas to the exact sampled extent.
    from build123d import Compound

    deflection = max(Compound(list(faces)).bounding_box().diagonal, 1e-9) / 4000
    if window is not None:
        # Zoomed maps need a finer mesh to stay smooth at the larger scale.
        window_span = max(window[2] - window[0], window[3] - window[1], 1e-9)
        deflection = min(deflection, window_span / 4000)
    outlines = []
    seen_edges = set()
    meshes = []
    for face in faces:
        vertices, triangles = face.tessellate(deflection, 0.2)
        meshes.append(([to_local(v) for v in vertices], triangles))
        for edge in face.edges():
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            outlines.append([to_local(p) for p in edge_points(edge, deflection)])
    if window is not None:
        bounds = tuple(window)
    else:
        xs = [x for line in outlines for x, _ in line]
        ys = [y for line in outlines for _, y in line]
        bounds = (min(xs), min(ys), max(xs), max(ys))
    scale, width, height, margin, draw_w, draw_h = _image_size(bounds, count)
    # Center the drawing inside the (possibly padded) drawing area.
    offset_x = margin + (draw_w - (bounds[2] - bounds[0]) * scale) / 2
    offset_y = margin + (draw_h - (bounds[3] - bounds[1]) * scale) / 2
    clip = (margin, margin, margin + draw_w, margin + draw_h)

    def to_pixel(x, y, factor=1):
        return (
            (offset_x + (x - bounds[0]) * scale) * factor,
            (offset_y + (bounds[3] - y) * scale) * factor,
        )

    def to_plane(px, py):
        return (
            bounds[0] + (px - offset_x) / scale,
            bounds[3] - (py - offset_y) / scale,
        )

    s = _SUPERSAMPLE
    ids_big = Image.new("I", (width * s, height * s), 0)
    ids_draw = ImageDraw.Draw(ids_big)
    # Geometry far outside a window is clamped so rasterizing stays cheap;
    # the visible part is unaffected.
    limit = 4 * max(width, height) * s

    def clamp(point):
        return (min(max(point[0], -limit), limit), min(max(point[1], -limit), limit))

    for index, (local_vertices, triangles) in enumerate(meshes, start=1):
        pixels = [clamp(to_pixel(x, y, factor=s)) for x, y in local_vertices]
        for a, b, c in triangles:
            ids_draw.polygon([pixels[a], pixels[b], pixels[c]], fill=index, outline=index)
    local_outline_edges = [
        [clamp(to_pixel(x, y, factor=s)) for x, y in line] for line in outlines
    ]

    ids_hi = np.array(ids_big, dtype=np.int32)
    inside_clip = np.zeros(ids_hi.shape, dtype=bool)
    clip_hi = [round(v * s) for v in clip]
    inside_clip[clip_hi[1]:clip_hi[3], clip_hi[0]:clip_hi[2]] = True
    if window is not None:
        ids_hi[~inside_clip] = 0
    ids = ids_hi[::s, ::s]
    colors = _assign_colors(_adjacency(ids, count), count)

    lut = np.zeros((count + 1, 3), dtype=np.uint8)
    lut[0] = _BACKGROUND_RGB
    lut[1:] = colors
    pixels_rgb = lut[ids_hi]

    # Faint coordinate grid on the background, lined up with the ticks.
    x_low, y_high = to_plane(clip[0], clip[1])
    x_high, y_low = to_plane(clip[2], clip[3])
    step = _nice_step(max(x_high - x_low, y_high - y_low))
    x_ticks = _tick_values(x_low, x_high, step)
    y_ticks = _tick_values(y_low, y_high, step)
    grid = Image.new("L", (width * s, height * s), 0)
    grid_draw = ImageDraw.Draw(grid)
    for value in x_ticks:
        px = to_pixel(value, 0, factor=s)[0]
        grid_draw.line([(px, 0), (px, height * s)], fill=1, width=s)
    for value in y_ticks:
        py = to_pixel(0, value, factor=s)[1]
        grid_draw.line([(0, py), (width * s, py)], fill=1, width=s)
    grid_mask = (np.asarray(grid) > 0) & (ids_hi == 0)
    pixels_rgb[grid_mask] = _GRID_RGB

    canvas = Image.fromarray(pixels_rgb, "RGB")
    draw = ImageDraw.Draw(canvas)
    line_width = max(2, round(1.5 * s))
    for points in local_outline_edges:
        draw.line(points, fill=_OUTLINE_RGB, width=line_width, joint="curve")
    if window is not None:
        # Outlines past the window edge are cut off like the fills.
        clipped = np.asarray(canvas).copy()
        clipped[~inside_clip] = _BACKGROUND_RGB
        clipped[~inside_clip & grid_mask] = _GRID_RGB
        canvas = Image.fromarray(clipped, "RGB")
        ImageDraw.Draw(canvas).rectangle(
            [clip_hi[0], clip_hi[1], clip_hi[2] - 1, clip_hi[3] - 1],
            outline=_FRAME_RGB, width=s,
        )
    image = canvas.resize((width, height), Image.LANCZOS)

    # Labels: inside where the box fits clear of outlines, else leader.
    interior = np.zeros_like(ids)
    tiny = [False] * count
    visible = [False] * count
    for index in range(1, count + 1):
        region = ids == index
        if region.any():
            visible[index - 1] = True
            interior[_erode(region, 2)] = index
            tiny[index - 1] = _is_tiny(region)
    # Regions with no pixel: thinner than a pixel, or outside the window.
    geometric_anchor = {}
    outside_window = set()
    # Each region's interior point in map pixels, when it is on the map
    # and falls on that region's own pixels (always, unless a window cuts
    # it off or the region is under a pixel thick).
    targets = [None] * count
    for i in range(count):
        if not visible[i]:
            continue
        px, py = to_pixel(*interiors[i])
        col, row = int(round(px)), int(round(py))
        if 0 <= row < ids.shape[0] and 0 <= col < ids.shape[1] and ids[row, col] == i + 1:
            targets[i] = (px, py)
    for i in range(count):
        if visible[i]:
            continue
        x, y = interiors[i]
        px, py = to_pixel(x, y)
        if window is not None and not (clip[0] <= px < clip[2] and clip[1] <= py < clip[3]):
            outside_window.add(i)
            continue
        tiny[i] = True
        geometric_anchor[i] = (
            int(min(max(round(px), 0), width - 1)),
            int(min(max(round(py), 0), height - 1)),
        )
    texts = [str(i) for i in range(count)]
    # Big labels for simple sketches; a smaller font only when it moves a
    # meaningful number of labels from leaders back inside their regions.
    long_side = max(width, height)
    worthwhile = max(2, -(-count // 10))
    best = None
    for fraction in _FONT_FRACTIONS:
        font_size = max(14, round(long_side * fraction))
        style = face_labels.LabelStyle(width, height, font_size=font_size)
        sizes = [style.box_size(text) for text in texts]
        placements = face_labels.fit_labels(
            interior, sizes, max_clearance=2 * font_size, targets=targets
        )
        outside = sum(p["status"] != face_labels.LABELED for p in placements)
        if best is None or best[0] - outside >= worthwhile:
            best = (outside, font_size, style, sizes, placements)
        if best[0] == 0:
            break
    _outside, font_size, style, sizes, placements = best

    shown = count - len(outside_window)
    caption = _caption(plane, count, shown, window)
    caption_style = face_labels.LabelStyle(width, height, font_size=max(12, round(font_size * 0.8)))
    caption_box = (8, 8, *caption_style.box_size(caption))
    blocked = [caption_box]
    for placement, size in zip(placements, sizes):
        if placement["status"] == face_labels.LABELED:
            blocked.append((*placement["position"], *size))

    leader_indexes = [
        i for i, placement in enumerate(placements)
        if placement["status"] != face_labels.LABELED and i not in outside_window
    ]
    anchors = []
    for i in leader_indexes:
        if targets[i] is not None:
            point = (int(round(targets[i][0])), int(round(targets[i][1])))
        else:
            point = geometric_anchor.get(i) or face_labels.interior_point(ids == i + 1)
        anchors.append(point)
    occupied = _dilate(ids > 0, 4)
    # Labels whose anchor is nearest the drawing's edge go first and point
    # toward that edge, so leaders fan outward instead of crossing.
    left, top = max(offset_x, clip[0]), max(offset_y, clip[1])
    right = min(offset_x + (bounds[2] - bounds[0]) * scale, clip[2])
    bottom = min(offset_y + (bounds[3] - bounds[1]) * scale, clip[3])
    edge_info = []
    for anchor in anchors:
        sides = [
            (anchor[0] - left, math.pi),
            (right - anchor[0], 0.0),
            (anchor[1] - top, -math.pi / 2),
            (bottom - anchor[1], math.pi / 2),
        ]
        edge_info.append(min(sides))
    order = sorted(range(len(anchors)), key=lambda k: (edge_info[k][0], leader_indexes[k]))
    placed = face_labels.place_leader_labels(
        occupied,
        [anchors[k] for k in order],
        [sizes[leader_indexes[k]] for k in order],
        blocked_boxes=blocked,
        gap=3,
        prefer_angles=[edge_info[k][1] for k in order],
    )
    leaders = [None] * len(anchors)
    for k, result in zip(order, placed):
        leaders[k] = result

    draw = ImageDraw.Draw(image)
    for i, anchor, leader in zip(leader_indexes, anchors, leaders):
        box_w, box_h = sizes[i]
        x, y = leader["position"]
        end = face_labels.leader_endpoint(anchor, (x, y), (box_w, box_h))
        draw.line([anchor, end], fill=(255, 255, 255), width=4)
        draw.line([anchor, end], fill=_LEADER_RGB, width=2)
        r = 3
        draw.ellipse([anchor[0] - r, anchor[1] - r, anchor[0] + r, anchor[1] + r],
                     fill=_LEADER_RGB, outline=(255, 255, 255))
    for i, placement in enumerate(placements):
        if placement["status"] == face_labels.LABELED:
            style.draw(image, texts[i], placement["position"])
    for i, leader in zip(leader_indexes, leaders):
        style.draw(image, texts[i], leader["position"])
    caption_style.draw(image, caption, caption_box[:2])

    tick_style = face_labels.LabelStyle(width, height, font_size=max(12, round(font_size * 0.75)))
    image = _add_axes(image, tick_style.font, x_ticks, y_ticks, step, to_pixel, clip)
    image.save(output_path)

    leader_info = {
        i: (leader, anchor) for i, anchor, leader in zip(leader_indexes, anchors, leaders)
    }
    results = []
    for i in range(count):
        if i in outside_window:
            entry = {"label": OUTSIDE_WINDOW}
        elif i in leader_info:
            leader, anchor = leader_info[i]
            entry = {"label": LEADER, "clear": leader["clear"],
                     "box": (*leader["position"], *sizes[i]), "anchor": anchor}
        else:
            entry = {"label": INSIDE, "box": (*placements[i]["position"], *sizes[i])}
        if i not in outside_window:
            entry["target"] = targets[i] or geometric_anchor.get(i)
        entry["color"] = face_labels.hex_color(colors[i])
        entry["tiny"] = tiny[i]
        results.append(entry)
    return results


def _add_axes(image, font, x_ticks, y_ticks, step, to_pixel, clip):
    """Add a ruler band below and left of the map with plane-local ticks."""
    from PIL import Image, ImageDraw

    width, height = image.size
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    def text_size(text):
        left, top, right, bottom = measure.textbbox((0, 0), text, font=font)
        return right - left, bottom - top, left, top

    x_texts = [_tick_text(v, step) for v in x_ticks]
    y_texts = [_tick_text(v, step) for v in y_ticks]
    tick, pad = 6, 6
    label_h = max(text_size(t)[1] for t in x_texts + ["0"])
    left_band = max(text_size(t)[0] for t in y_texts + ["0"]) + tick + 2 * pad
    bottom_band = label_h + tick + 2 * pad
    out = Image.new("RGB", (width + left_band, height + bottom_band), _BACKGROUND_RGB)
    out.paste(image, (left_band, 0))
    draw = ImageDraw.Draw(out)
    axis_y = height + 1
    axis_x = left_band - 2
    x0, y0, x1, y1 = left_band + clip[0], clip[1], left_band + clip[2], clip[3]
    draw.line([(x0, axis_y), (x1, axis_y)], fill=_AXIS_RGB, width=1)
    draw.line([(axis_x, y0), (axis_x, y1)], fill=_AXIS_RGB, width=1)
    for value, text in zip(x_ticks, x_texts):
        px = left_band + to_pixel(value, 0)[0]
        draw.line([(px, axis_y), (px, axis_y + tick)], fill=_AXIS_RGB, width=1)
        w, _h, left, top = text_size(text)
        draw.text((px - w / 2 - left, axis_y + tick + pad / 2 - top), text,
                  fill=_AXIS_TEXT_RGB, font=font)
    for value, text in zip(y_ticks, y_texts):
        py = to_pixel(0, value)[1]
        draw.line([(axis_x - tick, py), (axis_x, py)], fill=_AXIS_RGB, width=1)
        w, h, left, top = text_size(text)
        draw.text((axis_x - tick - pad / 2 - w - left, py - h / 2 - top), text,
                  fill=_AXIS_TEXT_RGB, font=font)
    # Axis names sit past the last tick label so they never touch it.
    x_name_left = x1 + pad
    if x_ticks:
        last_w = text_size(x_texts[-1])[0]
        x_name_left = max(x_name_left, left_band + to_pixel(x_ticks[-1], 0)[0] + last_w / 2 + pad)
    _w, _h, left, top = text_size("x")
    draw.text((x_name_left - left, axis_y + tick + pad / 2 - top), "x",
              fill=_AXIS_TEXT_RGB, font=font)
    w, h, left, top = text_size("y")
    draw.text((axis_x - tick - pad / 2 - w - left, max(0, y0 - h - pad) - top), "y",
              fill=_AXIS_TEXT_RGB, font=font)
    return out


def _caption(plane, count, shown=None, window=None):
    name = _plane_name(plane)
    where = f"plane {name}" if name else "sketch plane"
    noun = "region" if count == 1 else "regions"
    if window is not None:
        x0, y0, x1, y1 = (f"{value:g}" for value in window)
        return (
            f"{shown} of {count} {noun} in window x {x0}..{x1}, y {y0}..{y1} on "
            f"{where} - label i = regions[i]"
        )
    return f"{count} {noun} on {where} (x right, y up) - label i = regions[i]"
