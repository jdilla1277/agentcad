"""sketch_regions / show_regions: sketch profile picking."""

from __future__ import annotations

import math

import pytest
from build123d import (
    BuildSketch,
    Circle,
    Line,
    Location,
    Locations,
    Mode,
    Plane,
    Polyline,
    Pos,
    Rectangle,
    extrude,
)

from agentcad import api
from agentcad.runners import build123d as runner
from agentcad.sketch_regions import SketchRegionError, sketch_regions

R = 10
ROTATED = Plane(Location((5, 6, 7), (30, 20, 10)))


def _assert_partition(regions, expected_count, expected_area=None):
    assert len(regions) == expected_count
    assert all(face.is_valid for face in regions)
    if expected_area is not None:
        assert sum(face.area for face in regions) == pytest.approx(expected_area, rel=1e-6)
    for i in range(len(regions)):
        for j in range(i + 1, len(regions)):
            overlap = regions[i] & regions[j]
            area = sum(f.area for f in overlap.faces()) if overlap else 0.0
            assert area < 1e-6


@pytest.mark.parametrize(
    ("name", "items", "count", "area"),
    [
        ("two overlapping circles", [Circle(R), Pos(8, 0) * Circle(R)], 3, None),
        ("rect + full line", [Rectangle(40, 30), Line((-30, 0), (30, 5))], 2, 1200),
        ("rect + dangling line", [Rectangle(40, 30), Line((-30, 0), (0, 0))], 1, 1200),
        ("rect + internal line", [Rectangle(40, 30), Line((-5, 0), (5, 0))], 1, 1200),
        ("Venn", [Circle(R), Pos(8, 0) * Circle(R), Pos(4, 7) * Circle(R)], 7, None),
        ("disjoint", [Pos(-30, 0) * Rectangle(10, 10), Pos(30, 0) * Rectangle(10, 10)], 2, 200),
        ("shared side", [Pos(-5, 0) * Rectangle(10, 10), Pos(5, 0) * Rectangle(10, 10)], 2, 200),
        ("tangent circles", [Circle(R), Pos(2 * R, 0) * Circle(R)], 2, 2 * math.pi * R * R),
        ("internally tangent", [Circle(R), Pos(R / 2, 0) * Circle(R / 2)], 2, math.pi * R * R),
        ("wires only", [Rectangle(40, 30).wire(), Circle(5).wire()], 2, 1200),
        ("list argument", [[Circle(R), Pos(8, 0) * Circle(R)]], 3, None),
        (
            "edges only",
            [Line((0, 0), (10, 0)), Line((10, 0), (0, 10)), Line((0, 10), (0, 0)),
             Line((-1, 1), (11, 1))],
            2, 50,
        ),
        ("merged sketch split by a line",
         [Circle(R) + Pos(8, 0) * Circle(R), Line((4, -20), (4, 20))], 2, None),
    ],
)
def test_region_case_table(name, items, count, area):
    _assert_partition(sketch_regions(*items), count, area)


def test_rect_with_inner_circle_gives_ring_with_one_hole_first():
    regions = sketch_regions(Rectangle(40, 30), Circle(5))
    _assert_partition(regions, 2, 1200)
    assert len(regions[0].inner_wires()) == 1  # the ring, larger area first
    assert regions[1].area == pytest.approx(math.pi * 25)
    assert extrude(regions[0], amount=5).volume == pytest.approx(5 * (1200 - math.pi * 25))


@pytest.mark.parametrize("plane", [Plane.XZ, ROTATED], ids=["XZ", "rotated"])
def test_non_xy_planes_keep_plane_and_normal(plane):
    regions = sketch_regions(plane * Circle(R), plane * Pos(8, 0) * Circle(R))
    _assert_partition(regions, 3)
    assert (regions.plane.z_dir - plane.z_dir).length < 1e-9
    assert (regions.plane.origin - plane.origin).length < 1e-6
    for face in regions:
        assert face.normal_at().dot(plane.z_dir) == pytest.approx(1)
    # Local centers read in the plane's own coordinates, left to right.
    xs = [regions.plane.to_local_coords(f.center()).X for f in regions]
    assert xs == sorted(xs)
    solid = extrude(regions[1], amount=5)
    direction = (solid.center() - regions[1].center()).normalized()
    assert direction.dot(plane.z_dir) == pytest.approx(1, abs=1e-6)


def test_edges_only_on_named_plane_snap_to_it():
    regions = sketch_regions(
        Plane.XZ * Rectangle(40, 30).wire(), Plane.XZ * Line((-30, 0), (30, 0))
    )
    assert len(regions) == 2
    assert (regions.plane.z_dir - Plane.XZ.z_dir).length < 1e-9


def test_explicit_plane_string_is_accepted():
    regions = sketch_regions(Circle(R), Pos(8, 0) * Circle(R), plane="XY")
    assert len(regions) == 3


@pytest.mark.parametrize(
    "items",
    [[Circle(R), Plane.XZ * Circle(R)], [Circle(R), Pos(0, 0, 1) * Circle(R)]],
    ids=["crossing planes", "parallel offset"],
)
def test_non_coplanar_input_names_offset_and_plane(items):
    with pytest.raises(SketchRegionError, match=r"not coplanar.*off the sketch plane.*normal"):
        sketch_regions(*items)


def test_error_messages_are_actionable():
    with pytest.raises(SketchRegionError, match="got no geometry"):
        sketch_regions()
    with pytest.raises(SketchRegionError, match="open curves alone enclose nothing"):
        sketch_regions(Line((0, 0), (10, 0)), Line((0, 0), (0, 10)), Line((1, 1), (5, 5)))
    with pytest.raises(SketchRegionError, match="not a|solid"):
        from build123d import Box

        sketch_regions(Box(1, 1, 1))
    with pytest.raises(SketchRegionError, match="got str"):
        sketch_regions("circle")
    # A ValueError subclass, so generic handling keeps working.
    assert issubclass(SketchRegionError, ValueError)


def test_ordering_of_a_3x2_grid_is_rows_top_down_then_left_to_right():
    regions = sketch_regions(*[Pos(x, y) * Rectangle(4, 4) for x in (0, 10, 20) for y in (0, 10)])
    centers = [(round(f.center().X), round(f.center().Y)) for f in regions]
    assert centers == [(0, 10), (10, 10), (20, 10), (0, 0), (10, 0), (20, 0)]


def test_ordering_is_deterministic_regardless_of_input_order():
    items = [Circle(R), Pos(8, 0) * Circle(R), Pos(4, 7) * Circle(R)]
    first = sketch_regions(*items)
    second = sketch_regions(*reversed(items))
    assert [(round(f.center().X, 3), round(f.center().Y, 3)) for f in first] == [
        (round(f.center().X, 3), round(f.center().Y, 3)) for f in second
    ]


def test_min_area_drops_slivers_by_default_with_a_warning():
    items = [Circle(R), Pos(2 * R - 1e-5, 0) * Circle(R)]
    with pytest.warns(UserWarning, match="sliver"):
        default = sketch_regions(*items)
    assert len(default) == 2
    kept = sketch_regions(*items, min_area=0)
    assert len(kept) == 3
    assert min(f.area for f in kept) < 1e-5
    big_only = sketch_regions(Circle(R), Pos(8, 0) * Circle(R), min_area=156)
    assert len(big_only) == 1 and big_only[0].area == pytest.approx(158.53, abs=0.01)


def test_extruding_a_chosen_subset_is_valid():
    regions = sketch_regions(
        Rectangle(40, 20), Circle(6), Pos(15, 0) * Circle(6), Line((-20, 0), (20, 0))
    )
    part = extrude([regions[0], regions[2]], amount=5)
    assert part.is_valid
    assert part.volume == pytest.approx(5 * (regions[0].area + regions[2].area))


def test_adjacent_regions_extrude_into_one_fused_solid():
    regions = sketch_regions(Circle(R), Pos(8, 0) * Circle(R))
    part = extrude([regions[0], regions[1]], amount=5)
    assert part.is_valid
    assert len(part.solids()) == 1
    assert part.volume == pytest.approx(5 * (regions[0].area + regions[1].area))


def test_merged_build_sketch_loses_inner_boundaries_but_private_keeps_them():
    with BuildSketch() as merged:
        Circle(R)
        with Locations((8, 0)):
            Circle(R)
    assert len(sketch_regions(merged.sketch)) == 1
    with BuildSketch():
        a = Circle(R, mode=Mode.PRIVATE)
        b = Pos(8, 0) * Circle(R, mode=Mode.PRIVATE)
    assert len(sketch_regions(a, b)) == 3


def test_figure_eight_polyline_splits_at_its_crossing():
    regions = sketch_regions(Polyline((0, 0), (10, 10), (10, 0), (0, 10), close=True))
    assert len(regions) == 2


def test_api_names_are_exported_and_show_regions_needs_a_run():
    assert "sketch_regions" in api.__all__ and "show_regions" in api.__all__
    assert len(api.sketch_regions(Circle(R), Pos(8, 0) * Circle(R))) == 3
    with pytest.raises(RuntimeError, match="agentcad run"):
        api.show_regions([])


# ---------- runner capture ----------

SKETCH_ONLY = """\
regions = sketch_regions(Circle(10), Pos(8, 0) * Circle(10))
show_regions(regions)
"""


def test_runner_sketch_only_script_succeeds_without_shape():
    assert runner.validate(SKETCH_ONLY) == []
    result = runner.execute(SKETCH_ONLY)
    assert result.success
    assert result.topo_shape is None and result.native_shape is None
    assert result.output_type == "sketch"
    [entry] = result.region_maps
    assert entry["name"] == "regions"
    assert len(entry["faces"]) == 3


def test_runner_without_any_output_keeps_original_validation_message():
    source = "regions = sketch_regions(Circle(10))\n"
    [error] = runner.validate(source)
    assert error["check"] == "show_object_missing"
    assert "show_regions" not in error["message"]


def test_runner_rejects_duplicate_and_unsafe_map_names():
    duplicate = SKETCH_ONLY + "show_regions(regions)\n"
    result = runner.execute(duplicate)
    assert result.status == "execution_error"
    assert "already used" in result.exception and "name=" in result.exception
    unsafe = SKETCH_ONLY.replace("show_regions(regions)", "show_regions(regions, name='../x')")
    result = runner.execute(unsafe)
    assert result.status == "execution_error"
    assert "not usable as a file name" in result.exception


def test_runner_show_regions_rejects_non_faces_with_hint():
    result = runner.execute("show_regions([Circle(3).wire()])\n")
    assert result.status == "execution_error"
    assert "not a Face" in result.exception
    result = runner.execute("show_regions(Pos(10, 0) * Rectangle(4, 4) + Circle(3))\n")
    assert result.status == "execution_error"
    assert "sketch_regions" in result.exception


def test_runner_accepts_plain_face_lists_and_slices():
    source = (
        "regions = sketch_regions(Circle(10), Pos(8, 0) * Circle(10))\n"
        "show_regions(regions[1:], name='lens_and_right')\n"
        "show_regions([Rectangle(4, 4).face()], name='square')\n"
    )
    result = runner.execute(source)
    assert result.success
    assert [entry["name"] for entry in result.region_maps] == ["lens_and_right", "square"]
    assert len(result.region_maps[0]["faces"]) == 2


def test_runner_regions_are_not_part_of_the_captured_shape():
    source = (
        "regions = sketch_regions(Circle(10), Pos(8, 0) * Circle(10))\n"
        "show_regions(regions)\n"
        "show_object(extrude(regions[1], amount=2))\n"
    )
    result = runner.execute(source)
    assert result.success
    assert result.output_type == "single_part"
    assert len(result.parts) == 1
    assert len(result.region_maps) == 1


def test_cadquery_runner_flags_the_build123d_only_helpers():
    from agentcad.runners import cadquery

    errors = cadquery._check_b3d_only_helpers("show_regions(sketch_regions(1))\n")
    assert errors and "sketch_regions" in errors[0]["message"]


# ---------- region map rendering ----------

def test_region_map_labels_every_region_and_uses_leader_for_tiny_one(tmp_path):
    from PIL import Image

    from agentcad.region_map import render_region_map

    regions = sketch_regions(Rectangle(60, 40), Circle(12), Pos(11.7, 0) * Circle(0.6))
    tiny = min(range(len(regions)), key=lambda i: regions[i].area)
    out = tmp_path / "map.png"
    placements = render_region_map(list(regions), regions.plane, out)
    assert len(placements) == len(regions)
    assert placements[tiny]["label"] == "leader"
    assert all(p["label"] in {"inside", "leader"} for p in placements)
    largest = max(range(len(regions)), key=lambda i: regions[i].area)
    assert placements[largest]["label"] == "inside"
    with Image.open(out) as image:
        assert image.size[0] >= 900


def test_region_map_labels_sit_on_the_interior_points(tmp_path):
    from agentcad.region_map import render_region_map
    from agentcad.sketch_regions import describe_regions

    regions = sketch_regions(*CRESCENTS)
    faces = list(regions)
    interiors = [item["interior"] for item in describe_regions(faces, regions.plane)]
    placements = render_region_map(faces, regions.plane, tmp_path / "m.png",
                                   interiors=interiors)
    outside = regions.index(max(faces, key=lambda face: face.area))
    assert placements[outside]["label"] == "inside"
    for placement in placements:
        target = placement["target"]
        assert target is not None
        x, y, w, h = placement["box"]
        if placement["label"] == "inside":
            # Centered on the interior point, to the pixel.
            assert abs(x + w / 2 - target[0]) <= 1 and abs(y + h / 2 - target[1]) <= 1
        else:
            assert placement["anchor"] == (round(target[0]), round(target[1]))
    # The outer region's label is in the lower-left corner, where its
    # interior point is, not wherever the region happens to have room.
    corner = interiors[outside]
    assert corner[0] < -18 and corner[1] < -8


def test_region_map_colors_touching_regions_differently(tmp_path):
    from agentcad.region_map import render_region_map

    regions = sketch_regions(Circle(R), Pos(8, 0) * Circle(R))
    placements = render_region_map(list(regions), regions.plane, tmp_path / "m.png")
    assert placements[0]["color"] != placements[1]["color"]
    assert placements[1]["color"] != placements[2]["color"]


# ---------- picking by point, interior points, merging ----------

U_SHAPE = Polyline(
    (-10, 0), (10, 0), (10, 10), (6, 10), (6, 3), (-6, 3), (-6, 10), (-10, 10),
    close=True,
)


def test_u_shape_interior_point_is_inside_while_centroid_is_not():
    from agentcad.sketch_regions import describe_regions

    [u] = sketch_regions(U_SHAPE)
    plane = Plane.XY
    centroid = u.center()
    assert not u.is_inside(centroid)  # the centroid sits in the U's gap
    [item] = describe_regions([u], plane)
    x, y = item["interior"]
    assert item["interior_global"] == [x, y, 0.0]
    assert u.is_inside(plane.from_local_coords((x, y)))
    # Well inside, not grazing an edge: the U's arms are 4 wide, its base 3.
    from build123d import Vector

    assert min(edge.distance_to(Vector(x, y, 0)) for edge in u.edges()) >= 1.4
    assert sketch_regions(U_SHAPE).at((x, y)).area == pytest.approx(u.area)


CRESCENTS = (Rectangle(60, 40), Pos(-8, 0) * Circle(12), Pos(8, 0) * Circle(12),
             Pos(-20, 14) * Circle(0.6), Pos(-19.4, 14) * Circle(0.6))


# (sketch items, a point in the region, exact largest-inscribed-circle radius)
DEEP_CASES = {
    # Left circle minus the right one: the circle at (-12, 0) touches both arcs.
    "crescent": (CRESCENTS, (-14, 0), 8.0),
    # Arm-to-base corner: tangent to x=10, y=0 and the inner corner (6, 3).
    "u_shape": ((U_SHAPE,), (8, 1), 7 - math.sqrt(24)),
    "ring": ((Circle(10), Circle(4)), (7, 0), 3.0),
    # Two r=10 circles 0.5 apart: crescent 0.5 wide at its fattest.
    "sliver": ((Circle(10), Pos(0.5, 0) * Circle(10)), (-9.75, 0), 0.25),
    "lens": (CRESCENTS, (0, 0), 4.0),
}


@pytest.mark.parametrize("case", list(DEEP_CASES))
def test_interior_point_is_near_the_center_of_the_largest_inscribed_circle(case):
    from build123d import Vector

    from agentcad.sketch_regions import _classify, interior_point

    items, probe, radius = DEEP_CASES[case]
    regions = sketch_regions(*items)
    face = regions.at(probe)
    x, y = interior_point(face, regions.plane)
    assert _classify(face, Vector(x, y, 0), 1e-7) == "in"
    clearance = min(edge.distance_to(Vector(x, y, 0)) for edge in face.edges())
    assert 0.95 * radius <= clearance <= radius * 1.001 + 1e-9, (case, (x, y), clearance)
    assert interior_point(face, regions.plane) == (x, y)  # deterministic


def test_interior_points_stay_fast_for_many_regions():
    import time

    from agentcad.sketch_regions import describe_regions

    items = [Rectangle(120, 120), Pos(5, 5) * Circle(3)]
    for k in range(1, 12):
        v = -60 + 10 * k
        items += [Line((v, -60), (v, 60)), Line((-60, v), (60, v))]
    regions = sketch_regions(*items)
    assert len(regions) == 145
    start = time.perf_counter()
    describe_regions(list(regions), regions.plane)
    assert time.perf_counter() - start < 2.0  # ~0.1 s; generous for slow CI


@pytest.mark.parametrize("plane", [Plane.XY, Plane.XZ, ROTATED], ids=["XY", "XZ", "rotated"])
def test_interior_points_lie_inside_every_region(plane):
    from agentcad.sketch_regions import _classify, describe_regions

    regions = sketch_regions(
        plane * Rectangle(40, 30), plane * Circle(5), plane * U_SHAPE,
        plane * Line((-20, -12), (20, -12)),
    )
    for face, item in zip(regions, describe_regions(regions, regions.plane)):
        point = regions.plane.from_local_coords(tuple(item["interior"]))
        assert _classify(face, point, 1e-7) == "in"
        assert regions.at(tuple(item["interior"])) is face
        assert regions.at(tuple(item["interior_global"])) is face


def test_at_picks_regions_by_plane_local_and_global_points():
    regions = sketch_regions(U_SHAPE, Rectangle(40, 30))
    u = min(regions, key=lambda face: face.area)
    frame = max(regions, key=lambda face: face.area)
    assert regions.at((0, 1.5)) is u
    assert regions.at((0, 6)) is frame  # the U's gap belongs to the frame
    from build123d import Vector

    assert regions.at(Vector(8, 5, 0)) is u
    picked = regions.at((0, 1.5), (0, 12))
    assert list(picked) == [u, frame]
    assert extrude(picked, amount=2).is_valid

    on_xz = sketch_regions(Plane.XZ * Circle(R), Plane.XZ * Pos(8, 0) * Circle(R))
    [lens] = [face for face in on_xz if abs(face.center().X - 4) < 1e-6]
    assert on_xz.at((4, 0)) is lens  # plane-local x, y
    assert on_xz.at((4, 0, 0)) is lens  # the same point, global


def test_at_errors_name_the_nearest_region_and_a_point_inside():
    regions = sketch_regions(U_SHAPE, Rectangle(40, 30))
    with pytest.raises(SketchRegionError, match=r"no region.*Nearest is regions\[1\].*interior"):
        regions.at((100, 100))
    with pytest.raises(SketchRegionError, match=r"on the boundary of regions\[0\] and regions\[1\]"):
        regions.at((0, 0))
    with pytest.raises(SketchRegionError, match=r"3 off the sketch plane"):
        regions.at((0, 1.5, 3))
    with pytest.raises(SketchRegionError, match=r"both lie in regions\[0\]"):
        regions.at((0, 1.5), (8, 5))
    with pytest.raises(SketchRegionError, match=r"\(x, y\) plane-local"):
        regions.at("center")
    with pytest.raises(SketchRegionError, match="needs a point"):
        regions.at()
    assert issubclass(SketchRegionError, ValueError)


def test_at_and_merged_survive_slicing_but_not_filtering():
    regions = sketch_regions(Circle(R), Pos(8, 0) * Circle(R))
    tail = regions[1:]
    assert tail.plane is regions.plane
    assert tail.at((4, 0)) is regions[1]
    assert regions[1:2].merged(0).area == pytest.approx(regions[1].area)
    assert not hasattr(regions.filter_by(lambda face: True), "at")


def test_merged_regions_revolve_to_one_solid():
    from build123d import Axis, revolve

    # A ring profile beside the Z axis on XZ, cut into 4 by two lines: the
    # pieces share edges, like a lens barrel's stepped profile.
    profile = sketch_regions(
        Plane.XZ * Pos(10, 5) * Rectangle(4, 10),
        Plane.XZ * Line((8, 5), (12, 5)),
        Plane.XZ * Line((10, 0), (10, 10)),
    )
    assert len(profile) == 4
    separate = revolve(list(profile), Axis.Z)
    assert len(separate.solids()) > 1  # face-sharing solids in one part
    merged = profile.merged(0, 1, 2, 3)
    assert merged.area == pytest.approx(40)
    assert merged.normal_at().dot(profile.plane.z_dir) == pytest.approx(1)
    one = revolve(merged, Axis.Z)
    assert len(one.solids()) == 1 and one.is_valid
    by_point = profile.merged(profile.at((9, 2)), profile.at((11, 2)))
    assert by_point.area == pytest.approx(20)


def test_merged_rejects_regions_that_share_no_edge():
    corner = sketch_regions(
        Pos(-5, 5) * Rectangle(10, 10), Pos(5, -5) * Rectangle(10, 10)
    )
    with pytest.raises(SketchRegionError, match=r"regions\[0\], regions\[1\].*share no boundary edge"):
        corner.merged(0, 1)
    with pytest.raises(SketchRegionError, match="out of range"):
        corner.merged(0, 5)
    with pytest.raises(SketchRegionError, match="indexes or Faces"):
        corner.merged("0")


def test_merge_regions_api_works_on_plain_lists():
    regions = sketch_regions(Circle(R), Pos(8, 0) * Circle(R))
    plain = list(regions)
    merged = api.merge_regions(plain[0], plain[1], plain[2])
    assert merged.area == pytest.approx(sum(face.area for face in plain))
    assert len(extrude(merged, amount=1).solids()) == 1
    assert "merge_regions" in api.__all__


def test_show_regions_window_is_validated_in_the_script():
    bad_shape = SKETCH_ONLY.replace("show_regions(regions)", "show_regions(regions, window=(0, 0, 5, 5))")
    result = runner.execute(bad_shape)
    assert result.status == "execution_error"
    assert "((xmin, ymin), (xmax, ymax))" in result.exception
    empty = SKETCH_ONLY.replace("show_regions(regions)", "show_regions(regions, window=((5, 0), (0, 5)))")
    assert "xmin < xmax" in runner.execute(empty).exception
    away = SKETCH_ONLY.replace("show_regions(regions)", "show_regions(regions, window=((100, 100), (110, 110)))")
    assert "contains no region" in runner.execute(away).exception
    good = SKETCH_ONLY.replace("show_regions(regions)", "show_regions(regions, window=((-2, -2), (2, 2)))")
    result = runner.execute(good)
    assert result.success
    assert result.region_maps[0]["window"] == (-2.0, -2.0, 2.0, 2.0)


def test_region_map_window_draws_only_that_area_with_global_numbers(tmp_path):
    from PIL import Image

    from agentcad.region_map import render_region_map

    regions = sketch_regions(Rectangle(60, 40), Circle(12), Pos(11.7, 0) * Circle(0.6),
                             Pos(-20, 12) * Circle(4))
    full = render_region_map(list(regions), regions.plane, tmp_path / "full.png")
    assert all(entry["label"] != "outside_window" for entry in full)
    window = (9, -3, 15, 3)
    placements = render_region_map(list(regions), regions.plane, tmp_path / "win.png", window=window)
    in_window = {regions.index(regions.at(p)) for p in [(10, 0), (13, 0), (11.5, 0), (12.1, 0)]}
    for i, placement in enumerate(placements):
        if i in in_window:
            assert placement["label"] == "inside", (i, placement)
        else:
            assert placement["label"] == "outside_window", (i, placement)
        assert placement["tiny"] is False
    with Image.open(tmp_path / "win.png") as image:
        assert image.size[0] >= 900


def test_region_map_flags_tiny_regions_and_has_axis_band(tmp_path):
    from PIL import Image

    from agentcad.region_map import _nice_step, _tick_text, render_region_map

    regions = sketch_regions(
        Rectangle(60, 40), Pos(-20, 12) * Circle(0.3), Pos(-19.5, 12) * Circle(0.3)
    )
    lens = min(range(len(regions)), key=lambda i: regions[i].area)
    placements = render_region_map(list(regions), regions.plane, tmp_path / "m.png")
    assert placements[lens]["tiny"] is True
    assert placements[lens]["label"] == "leader"
    assert sum(p["tiny"] for p in placements) == 1
    with Image.open(tmp_path / "m.png") as image:
        # The ruler band below the map is not pure white (ticks + numbers).
        band = image.convert("L").crop((0, image.size[1] - 20, image.size[0], image.size[1]))
        assert band.getextrema()[0] < 128
    assert _nice_step(60) == 10 and _nice_step(6) == 1 and _nice_step(2) == 0.25
    assert _tick_text(-10.0, 10) == "-10" and _tick_text(0.25, 0.25) == "0.25"
    assert _tick_text(-0.0, 1) == "0"
