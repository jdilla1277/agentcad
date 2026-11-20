"""Region naming strategies: named curves (regions.where) vs points (regions.at).

The demo plate is a 60 x 30 rectangle, two r=10 circles at (+-8, 0) and a
split line on y=0: eight regions. The walkthrough build keeps both outer
bands, both lens halves, the upper-right moon and the lower-left moon. Each
edit below is one a designer makes; the expectations say which region each
description must find afterwards, or that it must refuse.
"""

from __future__ import annotations

import json

import pytest
from build123d import Circle, Line, Plane, Pos, Rectangle

from agentcad import region_naming
from agentcad.cli import cli
from agentcad.sketch_regions import SketchRegionError, describe_regions, sketch_regions

KEEP = {
    "top band": dict(outside=["c1", "c2"], above="split"),
    "bottom band": dict(outside=["c1", "c2"], below="split"),
    "lens, upper": dict(inside=["c1", "c2"], above="split"),
    "lens, lower": dict(inside=["c1", "c2"], below="split"),
    "upper-right moon": dict(inside=["c2"], outside=["c1"], above="split"),
    "lower-left moon": dict(inside=["c1"], outside=["c2"], below="split"),
}


def plate(c1=(-8, 0), c2=(8, 0), split_y=0.0, **kwargs):
    return sketch_regions(
        rect=Rectangle(60, 30), c1=Pos(*c1) * Circle(10), c2=Pos(*c2) * Circle(10),
        split=Line((-30, split_y), (30, split_y)), **kwargs,
    )


def picks(regions):
    out = {}
    for name, query in KEEP.items():
        try:
            out[name] = list(regions).index(regions.where(**query))
        except SketchRegionError as exc:
            out[name] = str(exc)
    return out


def items(regions):
    return describe_regions(regions, regions.plane, naming=regions.naming)


# ------------------------------------------------------- curves strategy
def test_map_selectors_keep_containment_and_just_enough_other_sides():
    selectors = [item["selector"] for item in items(plate())]
    assert selectors == [
        'regions.where(inside=["rect"], outside=["c1", "c2"], above="split")',
        'regions.where(inside=["rect", "c1"], outside=["c2"], above="split")',
        'regions.where(inside=["rect", "c2"], outside=["c1"], above="split")',
        'regions.where(inside=["rect", "c1", "c2"], above="split")',
        'regions.where(inside=["rect", "c1", "c2"], below="split")',
        'regions.where(inside=["rect", "c1"], outside=["c2"], below="split")',
        'regions.where(inside=["rect", "c2"], outside=["c1"], below="split")',
        'regions.where(inside=["rect"], outside=["c1", "c2"], below="split")',
    ]


def test_a_hole_moved_off_the_plate_is_refused_not_swapped_for_the_disk():
    before = sketch_regions(rect=Rectangle(60, 30), hole=Pos(10, 0) * Circle(5))
    selector = items(before)[1]["selector"]
    assert selector == 'regions.where(inside=["rect", "hole"])'
    after = sketch_regions(rect=Rectangle(60, 30), hole=Pos(50, 0) * Circle(5))
    with pytest.raises(SketchRegionError, match="matches no region"):
        eval(selector, {"regions": after})  # noqa: S307
    # The shorter description would have picked the disk outside the plate.
    assert after.where(inside=["hole"]).center().X == pytest.approx(50)


def test_records_carry_signature_edges_and_the_point_form():
    record = items(plate())[1]
    assert record["signature"] == {
        "inside": ["rect", "c1"], "outside": ["c2"], "above": ["split"],
    }
    assert record["edges"] == [["c1", "inside"], ["split", "above"], ["c2", "outside"]]
    x, y = record["interior"]
    assert record["point_selector"] == f"regions.at(({x:g}, {y:g}))"


def test_every_map_selector_finds_its_own_region():
    regions = plate()
    namespace = {"regions": regions}
    for index, item in enumerate(items(regions)):
        assert eval(item["selector"], namespace).is_same(regions[index])  # noqa: S307


@pytest.mark.parametrize("edit, expected", [
    ({}, [0, 7, 3, 4, 2, 5]),
    # Lifting c2 swaps the reading order of the moons (1<->2, 5<->6).
    ({"c2": (8, 4)}, [0, 7, 3, 4, 1, 6]),
    # Moving split up shrinks the upper lens half under its old point.
    ({"split_y": 3.0}, [0, 7, 3, 4, 2, 5]),
])
def test_descriptions_survive_edits_that_keep_the_same_crossings(edit, expected):
    assert list(picks(plate(**edit)).values()) == expected


def test_a_region_that_no_longer_exists_is_refused_by_name():
    found = picks(plate(c1=(-11, 0), c2=(11, 0)))  # circles stop overlapping
    assert found["top band"] == 0 and found["upper-right moon"] == 2
    for half in ("lens, upper", "lens, lower"):
        assert "matches no region" in found[half]
        assert "Inspect the new map" in found[half]


def test_a_description_that_splits_into_pieces_lists_them_with_selectors():
    regions = plate(c1=(-8, 6), c2=(8, 6))  # circles poke out of the top
    with pytest.raises(SketchRegionError) as exc:
        regions.where(outside=["c1", "c2"], above="split")
    message = str(exc.value)
    assert "matches 3 regions" in message
    assert 'touching=["c1", "split"]' in message and 'touching=["c2", "split"]' in message
    assert message.count('inside=["rect"]') == 3
    assert "where_all" in message
    assert len(regions.where_all(outside=["c1", "c2"], above="split")) == 3
    # Every region still gets a selector of its own.
    namespace = {"regions": regions}
    for index, item in enumerate(items(regions)):
        assert item["selector"].startswith("regions.where(")
        assert eval(item["selector"], namespace).is_same(regions[index])  # noqa: S307


def test_unknown_curve_names_and_bad_terms_are_refused():
    regions = plate()
    with pytest.raises(SketchRegionError, match=r"no curve named 'hole'; this sketch names rect, c1, c2, split"):
        regions.where(inside=["hole"])
    with pytest.raises(SketchRegionError, match="both above= and below="):
        regions.where(above="split", below="split")
    with pytest.raises(SketchRegionError, match="at least one term"):
        regions.where()
    with pytest.raises(SketchRegionError, match="matches 2 regions"):
        regions.where(inside=["c1", "c2"])


def test_dict_values_name_members_that_survive_insertion():
    holes = {"left": Pos(-15, 0) * Circle(3), "right": Pos(15, 0) * Circle(3)}
    regions = sketch_regions(plate=Rectangle(60, 30), holes=holes)
    selectors = [item["selector"] for item in items(regions)]
    assert selectors == [
        'regions.where(inside=["plate", "holes.left"])',
        'regions.where(inside=["plate"], outside=["holes"])',
        'regions.where(inside=["plate", "holes.right"])',
    ]
    edited = sketch_regions(plate=Rectangle(60, 30), holes={"mid": Circle(3), **holes})
    assert edited.where(inside=["plate", "holes.right"]).center().X == pytest.approx(15)
    assert len(edited.where_all(inside=["holes"])) == 3
    with pytest.raises(SketchRegionError, match="dict keys name curves"):
        sketch_regions(plate=Rectangle(60, 30), holes={"a b": Circle(3)})


def test_list_values_name_members_by_position_and_warn():
    with pytest.warns(UserWarning, match=r"names its curves by position.*pass a dict"):
        regions = sketch_regions(
            plate=Rectangle(60, 30), holes=[Pos(x, 0) * Circle(4) for x in (-15, 0, 15)]
        )
    selectors = [item["selector"] for item in items(regions)]
    assert 'regions.where(inside=["plate"], outside=["holes"])' in selectors
    assert 'regions.where(inside=["plate", "holes[1]"])' in selectors
    assert len(regions.where_all(inside=["holes"])) == 3
    assert regions.where(outside="holes").area == pytest.approx(60 * 30 - 3 * 3.14159265 * 16, rel=1e-6)
    assert regions.naming.map_info() == {
        "strategy": "curves", "curves": ["plate", "holes[0]", "holes[1]", "holes[2]"],
        "groups": ["holes"],
    }


def test_sides_follow_the_sketch_plane():
    pl = Plane.XZ
    regions = sketch_regions(
        rect=pl * Rectangle(60, 30), c1=pl * Circle(10), split=pl * Line((-30, 0), (30, 0))
    )
    top = regions.where(inside=["c1"], above="split")
    assert top.center().Z > 0  # plane-local "above" is +Z on XZ


def test_vertical_lines_split_left_and_right():
    regions = sketch_regions(rect=Rectangle(60, 30), cut=Line((5, -15), (5, 15)))
    assert [item["selector"] for item in items(regions)] == [
        'regions.where(inside=["rect"], left="cut")',
        'regions.where(inside=["rect"], right="cut")',
    ]


def test_slices_search_only_their_own_regions():
    regions = plate()
    assert len(regions[2:].where_all(inside=["c1"])) == 3  # lens halves + lower-left moon
    assert regions[2:].where(inside=["c1"], above="split").is_same(regions[3])


def test_unnamed_curves_fall_back_to_points():
    regions = sketch_regions(Rectangle(60, 30), Circle(10))
    item = items(regions)[0]
    assert item["selector"].startswith("regions.at((")
    assert "signature" not in item and "point_selector" not in item
    with pytest.raises(SketchRegionError, match="needs named curves"):
        regions.where(inside=["c1"])


@pytest.mark.parametrize("name", ["plane", "min_area", "naming"])
def test_reserved_keywords_cannot_name_a_curve(name):
    with pytest.raises(SketchRegionError, match=f"{name}= is reserved"):
        sketch_regions(rect=Rectangle(10, 10), **{name: Circle(2)})


# ------------------------------------------------------- points strategy
def test_points_strategy_keeps_todays_selectors():
    regions = plate(naming="points")
    record = items(regions)[0]
    assert record["selector"] == "regions.at((-22.998, 7.998))"
    assert {"signature", "edges", "point_selector"}.isdisjoint(record)
    with pytest.raises(SketchRegionError, match="region naming is set to 'points'"):
        regions.where(inside=["c1"])


def test_default_strategy_is_one_switch(monkeypatch):
    monkeypatch.setattr(region_naming, "DEFAULT_NAMING", "points")
    assert items(plate())[0]["selector"].startswith("regions.at((")
    monkeypatch.setattr(region_naming, "DEFAULT_NAMING", "curves")
    assert items(plate())[0]["selector"].startswith("regions.where(")


def test_unknown_strategy_is_refused():
    with pytest.raises(SketchRegionError, match="naming must be one of 'points', 'curves'"):
        plate(naming="fancy")


# ------------------------------------------------------------ end to end
SKETCH = """\
regions = sketch_regions(rect=Rectangle(60, 30), c1=Pos(-8, 0) * Circle(10),
                         c2=Pos(8, 0) * Circle(10), split=Line((-30, 0), (30, 0)))
show_regions(regions, name="plate")
"""

PART = """\
regions = sketch_regions(rect=Rectangle(60, 30), c1=Pos(-8, 0) * Circle(10),
                         c2=Pos({c2}) * Circle(10), split=Line((-30, 0), (30, 0)))
show_object(extrude({selector}, amount=5))
"""


@pytest.fixture
def project(runner, isolated_dir):
    result = runner.invoke(cli, ["init", "--name", "t", "--runtime", "build123d"])
    assert result.exit_code == 0, result.output
    (isolated_dir / "sketch.py").write_text(SKETCH)
    result = runner.invoke(
        cli, ["run", "sketch.py", "--label", "layout", "--no-view", "--no-daemon"]
    )
    assert result.exit_code == 0, result.output
    return isolated_dir


def test_saved_map_and_inspect_carry_the_named_selector(runner, project):
    listing = json.loads((project / "v1_layout_sketch/regions/plate.json").read_text())
    assert listing["naming"] == {"strategy": "curves", "curves": ["rect", "c1", "c2", "split"]}
    result = runner.invoke(cli, [
        "inspect", "v1_layout_sketch/regions/plate.json", "--id", "region_v1_plate_2",
    ])
    payload = json.loads(result.stdout)
    assert payload["selector"]["call"] == (
        'regions.where(inside=["rect", "c2"], outside=["c1"], above="split")'
    )
    assert payload["selector"]["point_call"].startswith("regions.at((")
    assert "keeps picking it after edits" in payload["selector"]["note"]
    summary = json.loads(runner.invoke(
        cli, ["inspect", "v1_layout_sketch/regions/plate.json"]
    ).stdout)
    assert summary["region_map"]["naming"]["strategy"] == "curves"
    assert summary["regions"][2]["signature"]["inside"] == ["rect", "c2"]


def test_pasted_selector_builds_the_same_moon_after_an_edit(runner, project):
    selector = json.loads(
        (project / "v1_layout_sketch/regions/plate.json").read_text()
    )["regions"][2]["selector"]
    areas = []
    for label, c2 in (("before", "8, 0"), ("after", "8, 4")):
        (project / "part.py").write_text(PART.format(c2=c2, selector=selector))
        result = runner.invoke(
            cli, ["run", "part.py", "--label", label, "--no-view", "--no-daemon"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        areas.append(payload["metrics"]["volume"] / 5)
    # Both runs extrude the upper-right moon, although after the lift its
    # region number changed from 2 to 1 (regions[2] is now the upper-left).
    lifted = plate(c2=(8, 4))
    assert areas[0] == pytest.approx(140.7295, rel=1e-4)
    assert areas[1] == pytest.approx(lifted[1].area, rel=1e-4)
    assert lifted[1].center().X > 0 and lifted[2].center().X < 0
