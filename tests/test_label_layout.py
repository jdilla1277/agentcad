"""Label placement for sketch region maps, without OpenGL."""

import numpy as np

from agentcad import label_layout
from agentcad.label_layout import (
    HIDDEN,
    LABELED,
    TOO_SMALL,
    fit_labels,
)


def _assert_inside(labels, number, position, size):
    x, y = position
    w, h = size
    assert (labels[y:y + h, x:x + w] == number).all()


def test_large_region_is_labeled_with_box_fully_inside():
    labels = np.zeros((100, 120), dtype=np.int32)
    labels[10:90, 20:110] = 1
    [result] = fit_labels(labels, [(30, 16)], max_clearance=10)
    assert result["status"] == LABELED
    _assert_inside(labels, 1, result["position"], (30, 16))


def test_absent_region_is_hidden_and_narrow_region_is_too_small():
    labels = np.zeros((60, 60), dtype=np.int32)
    labels[5:55, 10:18] = 2  # 8 px wide strip: visible but too narrow
    results = fit_labels(labels, [(12, 12), (12, 12)])
    assert results[0] == {"status": HIDDEN, "position": None}
    assert results[1] == {"status": TOO_SMALL, "position": None}


def test_box_never_straddles_a_hole_and_prefers_clearance():
    # A plate with a centered hole: the centroid falls inside the hole, so a
    # naive centroid placement would put the label over another region.
    labels = np.ones((200, 300), dtype=np.int32)
    yy, xx = np.mgrid[:200, :300]
    labels[(xx - 150) ** 2 + (yy - 100) ** 2 < 60 ** 2] = 2
    size = (24, 18)
    [plate, hole] = fit_labels(labels, [size, size], max_clearance=20)
    assert plate["status"] == LABELED and hole["status"] == LABELED
    _assert_inside(labels, 1, plate["position"], size)
    _assert_inside(labels, 2, hole["position"], size)
    x, y = plate["position"]
    clearance_box = labels[y - 20:y + size[1] + 20, x - 20:x + size[0] + 20]
    assert clearance_box.shape == (size[1] + 40, size[0] + 40)
    assert (clearance_box == 1).all()


def test_exact_fit_is_accepted_without_clearance():
    labels = np.zeros((40, 40), dtype=np.int32)
    labels[10:22, 5:35] = 1  # exactly 30x12
    [result] = fit_labels(labels, [(30, 12)], max_clearance=8)
    assert result == {"status": LABELED, "position": (5, 10)}


def test_disconnected_region_uses_a_piece_that_fits():
    labels = np.zeros((50, 100), dtype=np.int32)
    labels[0:50, 0:6] = 1  # sliver, too thin
    labels[10:40, 60:100] = 1  # large piece
    [result] = fit_labels(labels, [(20, 10)])
    assert result["status"] == LABELED
    assert result["position"][0] >= 60


def test_label_size_scales_with_image():
    assert label_layout.label_font_size(64, 48) == 10
    assert label_layout.label_font_size(800, 600) == 24
    assert label_layout.label_font_size(2400, 1800) == 72


# ---------- leader labels (region maps) ----------

def _boxes_overlap(a, b):
    (ax, ay), (aw, ah) = a
    (bx, by), (bw, bh) = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def test_leader_label_sits_on_background_near_its_anchor():
    occupied = np.zeros((200, 300), dtype=bool)
    occupied[50:150, 50:250] = True  # the drawing
    [result] = label_layout.place_leader_labels(occupied, [(100, 100)], [(20, 14)])
    x, y = result["position"]
    assert result["clear"] is True
    assert not occupied[y:y + 14, x:x + 20].any()
    # Nearest free background: just outside the drawing, not across the image.
    assert abs((x + 10) - 100) < 60 or abs((y + 7) - 100) < 70


def test_leader_labels_never_overlap_each_other_or_blocked_boxes():
    occupied = np.zeros((200, 200), dtype=bool)
    occupied[60:140, 60:140] = True
    anchors = [(100, 100)] * 6
    sizes = [(24, 14)] * 6
    blocked = [(70, 30, 60, 20)]
    results = label_layout.place_leader_labels(
        occupied, anchors, sizes, blocked_boxes=blocked, gap=2,
    )
    boxes = [(r["position"], s) for r, s in zip(results, sizes)]
    for i, a in enumerate(boxes):
        assert not _boxes_overlap(a, ((70, 30), (60, 20)))
        assert all(not _boxes_overlap(a, b) for b in boxes[i + 1:])
    assert all(r["clear"] for r in results)


def test_leader_line_does_not_cross_another_label():
    # A label box sits right above the anchor; the new label must go
    # somewhere its leader line does not pass through that box.
    occupied = np.zeros((160, 160), dtype=bool)
    occupied[70:90, 70:90] = True
    blocked = [(60, 30, 40, 20)]
    [result] = label_layout.place_leader_labels(
        occupied, [(80, 80)], [(16, 12)], blocked_boxes=blocked,
        prefer_angles=[-np.pi / 2],  # prefer straight up, through the box
    )
    x, y = result["position"]
    end = label_layout.leader_endpoint((80, 80), (x, y), (16, 12))
    for t in np.linspace(0, 1, 50):
        px = 80 + (end[0] - 80) * t
        py = 80 + (end[1] - 80) * t
        assert not (60 <= px < 100 and 30 <= py < 50)


def test_leader_falls_back_to_overlap_when_no_background_is_free():
    occupied = np.ones((40, 40), dtype=bool)
    [result] = label_layout.place_leader_labels(occupied, [(20, 20)], [(10, 8)])
    assert result["clear"] is False
    x, y = result["position"]
    assert 0 <= x <= 30 and 0 <= y <= 32


def test_target_centers_the_box_when_it_fits_else_nearest_fit():
    labels = np.zeros((100, 200), dtype=np.int32)
    labels[10:90, 10:190] = 1
    [placed] = fit_labels(labels, [(20, 10)], max_clearance=20, targets=[(40, 30)])
    assert placed["status"] == LABELED and placed["position"] == (30, 25)
    # Too close to the border to center there: fits as near the target as it can.
    [nearest] = fit_labels(labels, [(20, 10)], max_clearance=0, targets=[(12, 30)])
    assert nearest["position"] == (10, 25)
    # No target keeps the centroid preference.
    [plain] = fit_labels(labels, [(20, 10)], max_clearance=0)
    assert plain["position"] == (89, 44)


def test_interior_point_is_deep_inside_and_none_when_empty():
    mask = np.zeros((50, 80), dtype=bool)
    mask[10:40, 10:70] = True
    x, y = label_layout.interior_point(mask)
    assert 20 <= x <= 60 and 20 <= y <= 30
    assert label_layout.interior_point(np.zeros((5, 5), dtype=bool)) is None


def test_label_style_accepts_explicit_font_size():
    style = label_layout.LabelStyle(800, 600, font_size=31)
    assert style.font_size == 31

