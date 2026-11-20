"""Number-label placement for sketch region maps.

``show_regions`` paints each region a flat color into an image, then this
module decides where (and whether) each region's number fits as a label
inside its visible pixels, or places it outside with a leader line. It is
pure numpy/PIL so the placement logic is testable without OpenGL.
"""

from __future__ import annotations

# Saturated hues ordered so the first few are maximally distinct from each
# other and from a gray/white background.
REGION_PALETTE = (
    (230, 25, 75),    # red
    (67, 99, 216),    # blue
    (60, 180, 75),    # green
    (245, 130, 49),   # orange
    (145, 30, 180),   # purple
    (66, 212, 244),   # cyan
    (240, 50, 230),   # magenta
    (255, 225, 25),   # yellow
)

LABELED = "labeled"
TOO_SMALL = "too_small"
HIDDEN = "hidden"


def hex_color(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def label_font_size(width, height):
    """Label text size in pixels, scaled with the output image."""
    return max(10, min(72, round(min(width, height) * 0.04)))


def _summed_area_table(mask):
    import numpy as np

    height, width = mask.shape
    table = np.zeros((height + 1, width + 1), dtype=np.int32)
    table[1:, 1:] = mask.astype(np.int32).cumsum(axis=0).cumsum(axis=1)
    return table


def label_bounds(labels, count):
    """Tight bounds (y0, y1, x0, x1) of each label 1..count in ``labels``,
    as slice ends, or None for a label with no pixel. One pass over the
    image, so per-label work can run on a small crop instead."""
    import numpy as np

    labels = np.asarray(labels)
    ys, xs = np.nonzero(labels)
    values = labels[ys, xs]
    keep = (values >= 1) & (values <= count)
    ys, xs, values = ys[keep], xs[keep], values[keep]
    big = np.iinfo(np.int64).max
    y0 = np.full(count + 1, big, dtype=np.int64)
    x0 = np.full(count + 1, big, dtype=np.int64)
    y1 = np.full(count + 1, -1, dtype=np.int64)
    x1 = np.full(count + 1, -1, dtype=np.int64)
    np.minimum.at(y0, values, ys)
    np.minimum.at(x0, values, xs)
    np.maximum.at(y1, values, ys)
    np.maximum.at(x1, values, xs)
    return [
        None if y1[k] < 0 else (int(y0[k]), int(y1[k]) + 1, int(x0[k]), int(x1[k]) + 1)
        for k in range(1, count + 1)
    ]


def _fitting_boxes(table, box_w, box_h):
    """Top-left (xs, ys) of every box_w x box_h box fully inside the mask."""
    import numpy as np

    height, width = table.shape[0] - 1, table.shape[1] - 1
    if box_w < 1 or box_h < 1 or box_w > width or box_h > height:
        return None
    rows = height - box_h + 1
    cols = width - box_w + 1
    filled = (
        table[box_h:, box_w:]
        - table[:rows, box_w:]
        - table[box_h:, :cols]
        + table[:rows, :cols]
    )
    ys, xs = np.nonzero(filled == box_w * box_h)
    if len(xs) == 0:
        return None
    return xs, ys


def _place_box(mask, box_w, box_h, max_clearance, target=None):
    """Top-left (x, y) for a box inside ``mask``, or None if it cannot fit.

    With a ``target`` (x, y) pixel, a box centered on it wins whenever it
    fits. Otherwise prefers the most clearance from the region's border up
    to ``max_clearance`` pixels, then the spot closest to ``target`` (or to
    the region centroid when there is no target).
    """
    import numpy as np

    table = _summed_area_table(mask)
    if _fitting_boxes(table, box_w, box_h) is None:
        return None
    if target is not None:
        x = int(round(target[0] - box_w / 2))
        y = int(round(target[1] - box_h / 2))
        height, width = mask.shape
        if (0 <= x <= width - box_w and 0 <= y <= height - box_h
                and _box_sum(table, x, y, box_w, box_h) == box_w * box_h):
            return x, y
    low, high = 0, max(0, int(max_clearance))
    while low < high:  # largest clearance that still fits (fits is monotone)
        mid = (low + high + 1) // 2
        if _fitting_boxes(table, box_w + 2 * mid, box_h + 2 * mid) is None:
            high = mid - 1
        else:
            low = mid
    clearance = low
    outer_w, outer_h = box_w + 2 * clearance, box_h + 2 * clearance
    xs, ys = _fitting_boxes(table, outer_w, outer_h)
    if target is not None:
        cx, cy = float(target[0]), float(target[1])
    else:
        ys_all, xs_all = np.nonzero(mask)
        cx, cy = float(xs_all.mean()), float(ys_all.mean())
    distance = (xs + outer_w / 2 - cx) ** 2 + (ys + outer_h / 2 - cy) ** 2
    best = int(np.argmin(distance))
    return int(xs[best]) + clearance, int(ys[best]) + clearance


def fit_labels(labels, box_sizes, max_clearance=0, targets=None):
    """Place one label box per region inside that region's visible pixels.

    Args:
        labels: (H, W) int array; value k marks pixels of the k-th region
            (1-based), 0 is anything else.
        box_sizes: one (width, height) label box per region, in order.
        max_clearance: how far (px) placement tries to keep the box away from
            the region border. More clearance is preferred up to this cap;
            zero clearance (box merely fits) is still accepted.
        targets: optional list parallel to ``box_sizes`` of preferred label
            centers (x, y) in image pixels, or None per region. A box centered
            on its target is used whenever it fits; otherwise the box goes
            where it fits closest to the target.

    Returns:
        A list parallel to ``box_sizes`` of dicts with ``status``
        (``labeled`` / ``too_small`` / ``hidden``) and ``position`` (top-left
        (x, y) of the box in image pixels, or None). The whole box always lies
        on the region's own visible pixels.
    """
    import numpy as np

    labels = np.asarray(labels)
    bounds = label_bounds(labels, len(box_sizes))
    results = []
    for number, (box_w, box_h) in enumerate(box_sizes, start=1):
        if bounds[number - 1] is None:
            results.append({"status": HIDDEN, "position": None})
            continue
        y0, y1, x0, x1 = bounds[number - 1]
        crop = labels[y0:y1, x0:x1] == number
        target = targets[number - 1] if targets is not None else None
        if target is not None:
            target = (target[0] - x0, target[1] - y0)
        position = _place_box(crop, box_w, box_h, max_clearance, target)
        if position is None:
            results.append({"status": TOO_SMALL, "position": None})
        else:
            results.append({
                "status": LABELED,
                "position": (position[0] + x0, position[1] + y0),
            })
    return results


class LabelStyle:
    """Font and box geometry shared by measuring and drawing labels."""

    def __init__(self, width, height, font_size=None):
        from agentcad.render import _image_label_font

        self.font_size = font_size or label_font_size(width, height)
        self.font = _image_label_font(self.font_size)
        self.padding = max(2, self.font_size // 4)
        self.border = max(1, self.font_size // 12)
        self.radius = max(2, self.font_size // 4)

    def _text_bbox(self, text):
        from PIL import Image, ImageDraw

        return ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox(
            (0, 0), text, font=self.font
        )

    def box_size(self, text):
        left, top, right, bottom = self._text_bbox(text)
        return (
            (right - left) + 2 * self.padding,
            (bottom - top) + 2 * self.padding,
        )

    def draw(self, image, text, position):
        from PIL import ImageDraw

        left, top, _right, _bottom = self._text_bbox(text)
        box_w, box_h = self.box_size(text)
        x, y = position
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            [(x, y), (x + box_w - 1, y + box_h - 1)],
            radius=self.radius,
            fill=(255, 255, 255),
            outline=(25, 25, 25),
            width=self.border,
        )
        draw.text(
            (x + self.padding - left, y + self.padding - top),
            text,
            fill=(20, 20, 20),
            font=self.font,
        )


def _box_sum(table, x, y, box_w, box_h):
    return int(
        table[y + box_h, x + box_w] - table[y, x + box_w]
        - table[y + box_h, x] + table[y, x]
    )


def _nearest_on_box(point, x, y, box_w, box_h):
    return (
        min(max(point[0], x), x + box_w - 1),
        min(max(point[1], y), y + box_h - 1),
    )


def _segment_hits(mask, start, end, skip=4.0):
    """Number of sampled points on start->end (after ``skip`` px) inside mask."""
    import math

    import numpy as np

    length = math.hypot(end[0] - start[0], end[1] - start[1])
    if length <= skip:
        return 0
    height, width = mask.shape
    steps = int(length / 2) + 1
    t = np.arange(steps + 1) / steps
    t = t[t * length >= skip]
    # rint rounds halves to even, like round() did per point.
    px = np.rint(start[0] + (end[0] - start[0]) * t).astype(np.int64)
    py = np.rint(start[1] + (end[1] - start[1]) * t).astype(np.int64)
    inside = (px >= 0) & (px < width) & (py >= 0) & (py < height)
    return int(np.count_nonzero(mask[py[inside], px[inside]]))


def _mark_segment(mask, start, end, radius):
    import math

    height, width = mask.shape
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    steps = int(length / 2) + 1
    for k in range(steps + 1):
        t = k / steps
        px = int(round(start[0] + (end[0] - start[0]) * t))
        py = int(round(start[1] + (end[1] - start[1]) * t))
        mask[max(0, py - radius):min(height, py + radius + 1),
             max(0, px - radius):min(width, px + radius + 1)] = True


def leader_endpoint(anchor, position, box_size):
    """Point on the label box where its leader line from ``anchor`` ends."""
    return _nearest_on_box(anchor, position[0], position[1], *box_size)


def place_leader_labels(occupied, anchors, box_sizes, *, blocked_boxes=(),
                        gap=4, away_from=None, prefer_angles=None, angles=24,
                        extra_rings=3):
    """Place label boxes outside the drawing, each near its anchor point.

    Used when a region is too small for its label: the label sits nearby on
    empty background and a leader line connects it to ``anchor``.

    A candidate is *clear* when its box covers no drawing pixel, overlaps no
    other box or leader line, and its leader line crosses no other box.
    The nearest clear candidate wins, preferring (within a few more search
    rings) one whose line crosses no earlier leader line.

    Args:
        occupied: (H, W) bool array of drawing pixels a box should not cover.
        anchors: one (x, y) pixel point per label (inside its region).
        box_sizes: one (width, height) per label.
        blocked_boxes: (x, y, w, h) boxes already drawn (inside labels,
            captions); new boxes and lines never overlap these.
        gap: minimum pixel spacing kept around boxes.
        away_from: optional (x, y); search starts in the direction pointing
            from it toward the anchor (outward from the drawing's center).
        prefer_angles: optional per-label start direction in radians
            (image coordinates, y down); overrides ``away_from``.
        angles: number of directions tried per search ring.
        extra_rings: rings searched past the first clear candidate to find
            one whose leader crosses no other leader.

    Returns:
        A list parallel to ``anchors`` of dicts with ``position`` (top-left
        (x, y)) and ``clear`` (False when no clear spot existed and the box
        overlaps the drawing or its line crosses another label).
    """
    import math

    import numpy as np

    occupied = np.asarray(occupied, dtype=bool)
    height, width = occupied.shape
    occupied_table = _summed_area_table(occupied)
    boxes = np.zeros_like(occupied)
    lines = np.zeros_like(occupied)

    def block(x, y, box_w, box_h):
        boxes[max(0, y - gap):y + box_h + gap, max(0, x - gap):x + box_w + gap] = True

    for x, y, box_w, box_h in blocked_boxes:
        block(int(x), int(y), int(box_w), int(box_h))

    results = []
    for number, (anchor, (box_w, box_h)) in enumerate(zip(anchors, box_sizes)):
        anchor = (float(anchor[0]), float(anchor[1]))
        if prefer_angles is not None:
            base = prefer_angles[number]
        elif away_from is not None and anchor != tuple(away_from):
            base = math.atan2(anchor[1] - away_from[1], anchor[0] - away_from[0])
        else:
            base = -math.pi / 4
        # Alternate around the preferred direction: 0, +1, -1, +2, -2, ...
        offsets = [0]
        for step in range(1, angles // 2 + 1):
            offsets.extend((step, -step))
        directions = [base + 2 * math.pi * k / angles for k in offsets[:angles]]
        unit = [(math.cos(angle), math.sin(angle)) for angle in directions]
        radius = max(box_w, box_h) / 2 + gap + 4
        step_r = max(4, box_h / 2)
        limit = math.hypot(width, height)
        clear_best = None  # (line_crossings, ring, order, x, y)
        fallback = None    # (box_crossings, overlap, ring, x, y)
        stop_ring = None
        ring = 0
        while radius <= limit and (stop_ring is None or ring <= stop_ring):
            for order, (cos_a, sin_a) in enumerate(unit):
                x = int(round(anchor[0] + radius * cos_a - box_w / 2))
                y = int(round(anchor[1] + radius * sin_a - box_h / 2))
                if x < 1 or y < 1 or x + box_w >= width or y + box_h >= height:
                    continue
                # boxes and lines change after every label, so read them
                # directly; a box is small, and rebuilding a whole-image
                # lookup table per label was the slow part on busy maps.
                if boxes[y:y + box_h, x:x + box_w].any():
                    continue
                end = _nearest_on_box(anchor, x, y, box_w, box_h)
                box_crossings = _segment_hits(boxes, anchor, end)
                overlap = _box_sum(occupied_table, x, y, box_w, box_h)
                on_lines = int(np.count_nonzero(lines[y:y + box_h, x:x + box_w]))
                if overlap == 0 and box_crossings == 0 and on_lines == 0:
                    key = (_segment_hits(lines, anchor, end), ring, order, x, y)
                    if clear_best is None or key < clear_best:
                        clear_best = key
                    if stop_ring is None:
                        stop_ring = ring + extra_rings
                    if key[0] == 0:
                        stop_ring = min(stop_ring, ring)
                else:
                    key = (box_crossings, on_lines, overlap, ring, x, y)
                    if fallback is None or key < fallback:
                        fallback = key
            radius += step_r
            ring += 1
        if clear_best is not None:
            x, y, clear = clear_best[3], clear_best[4], True
        elif fallback is not None:
            x, y, clear = fallback[4], fallback[5], False
        else:
            x = int(min(max(anchor[0] - box_w / 2, 0), max(width - box_w, 0)))
            y = int(min(max(anchor[1] - box_h / 2, 0), max(height - box_h, 0)))
            clear = False
        block(x, y, box_w, box_h)
        _mark_segment(lines, anchor, _nearest_on_box(anchor, x, y, box_w, box_h), 1)
        results.append({"position": (x, y), "clear": clear})
    return results


def interior_point(mask):
    """(x, y) of the pixel deepest inside ``mask`` (closest to its centroid
    among the deepest), or None when the mask is empty."""
    import numpy as np

    mask = np.asarray(mask, dtype=bool)
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    x0, y0 = int(xs.min()), int(ys.min())
    crop = mask[y0:int(ys.max()) + 1, x0:int(xs.max()) + 1]
    position = _place_box(crop, 1, 1, max(crop.shape))
    return (position[0] + x0, position[1] + y0)
