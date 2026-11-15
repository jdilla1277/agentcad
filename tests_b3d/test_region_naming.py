"""Region naming strategy plumbing: the points strategy keeps today's selectors."""

from __future__ import annotations

import pytest
from build123d import Circle, Pos, Rectangle

from agentcad import region_naming
from agentcad.sketch_regions import SketchRegionError, describe_regions, sketch_regions


def plate(**kwargs):
    return sketch_regions(
        Rectangle(60, 30), Pos(-8, 0) * Circle(10), Pos(8, 0) * Circle(10), **kwargs
    )


def test_points_strategy_keeps_point_selectors():
    regions = plate(naming="points")
    record = describe_regions(regions, regions.plane, naming=regions.naming)[0]
    assert record["selector"].startswith("regions.at((")
    assert set(record) >= {"index", "selector", "bounds"}
    assert regions.naming.map_info() == {"strategy": "points"}


def test_default_strategy_is_one_switch(monkeypatch):
    assert region_naming.DEFAULT_NAMING == "points"
    assert plate().naming.strategy == "points"
    monkeypatch.setattr(region_naming, "DEFAULT_NAMING", "nonexistent")
    with pytest.raises(SketchRegionError, match="naming must be one of 'points'"):
        plate()


def test_slices_and_at_keep_the_naming_context():
    regions = plate()
    assert regions[1:].naming is regions.naming
    assert regions.at((-8, 0), (8, 0)).naming is regions.naming


def test_unknown_strategy_is_refused():
    with pytest.raises(SketchRegionError, match="naming must be one of 'points'"):
        plate(naming="fancy")
