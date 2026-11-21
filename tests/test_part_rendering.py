"""Saved appearances bind to complete reloaded geometry, not child order."""
import pytest
from pathlib import Path
from build123d import Box, Compound, Edge, export_step

from agentcad.metrics import compute_metrics
from agentcad.part_rendering import saved_render_parts
from agentcad.step_io import load_cad_shape


def round_trip(tmp_path, shapes):
    path = tmp_path / "saved.step"
    export_step(Compound(children=shapes), path)
    return load_cad_shape(path)


def records(shapes):
    return [
        {"id": f"part_{i}", "color": color, "metrics": compute_metrics(shape.wrapped)}
        for i, (shape, color) in enumerate(zip(shapes, ("red", "blue", "green")))
    ]


def test_restores_reordered_repeated_parts_at_their_actual_positions(tmp_path):
    shapes = [Box(10, 12, 6).translate((-20, 0, 0)), Box(10, 12, 6).translate((20, 0, 0))]
    saved = records(shapes)
    restored = saved_render_parts(round_trip(tmp_path, shapes), list(reversed(saved)))
    assert [p["color"] for p in restored] == ["blue", "red"]
    for part in restored:
        measured = compute_metrics(part["topo_shape"])
        assert measured["bounding_box"] == part["metrics"]["bounding_box"]
        assert measured["center_of_mass"] == part["metrics"]["center_of_mass"]


def test_preserves_nested_multi_solid_part_as_one_colored_part(tmp_path):
    pair = Compound(children=[Box(6, 8, 4).translate((-25, 0, 0)), Box(6, 8, 4).translate((-10, 0, 0))])
    shapes = [pair, Box(10, 8, 4).translate((20, 0, 0))]
    restored = saved_render_parts(round_trip(tmp_path, shapes), records(shapes))
    assert restored is not None and len(restored) == 2
    assert compute_metrics(restored[0]["topo_shape"])["volume"] == records(shapes)[0]["metrics"]["volume"]
    assert compute_metrics(restored[0]["topo_shape"])["face_count"] == 12


def test_single_part_wrapper_is_not_an_ambiguous_match(tmp_path):
    shapes = [Box(10, 12, 6)]
    restored = saved_render_parts(round_trip(tmp_path, shapes), records(shapes))
    assert len(restored) == 1 and restored[0]["color"] == "red"


def test_real_manifold_step_round_trip_restores_saved_color(tmp_path):
    from agentcad.step_io import write_step_shape

    source = Path(__file__).parent / "fixtures/real_world/pump_manifold.step"
    original = load_cad_shape(source)
    metadata = [{"id": "manifold", "color": "steelblue", "metrics": compute_metrics(original)}]
    saved = tmp_path / "manifold.step"
    write_step_shape(original, saved)
    restored = saved_render_parts(load_cad_shape(saved), metadata)
    assert restored is not None and restored[0]["color"] == "steelblue"
    assert compute_metrics(restored[0]["topo_shape"])["face_count"] == metadata[0]["metrics"]["face_count"]


@pytest.mark.parametrize("damage", ["absent", "partial", "duplicate", "metrics", "bbox", "nan", "wrong_type"])
def test_incomplete_or_changed_metadata_falls_back_to_whole_model(tmp_path, damage):
    shapes = [Box(10, 12, 6).translate((-20, 0, 0)), Box(10, 12, 6).translate((20, 0, 0))]
    saved = records(shapes)
    if damage == "absent":
        saved = []
    elif damage == "partial":
        saved = saved[:1]
    elif damage == "duplicate":
        saved = [saved[0], saved[0]]
    elif damage == "metrics":
        saved[0].pop("metrics")
    elif damage == "bbox":
        saved[0]["metrics"]["bounding_box"]["x"][0] += 10
    elif damage == "nan":
        saved[0]["metrics"]["volume"] = float("nan")
    else:
        saved = [None]
    assert saved_render_parts(round_trip(tmp_path, shapes), saved) is None


def test_coincident_indistinguishable_parts_do_not_guess_colors(tmp_path):
    shapes = [Box(10, 12, 6), Box(10, 12, 6)]
    assert saved_render_parts(round_trip(tmp_path, shapes), records(shapes)) is None


def test_face_coverage_cannot_hide_an_unrecorded_wire(tmp_path):
    box = Box(10, 12, 6)
    loaded = round_trip(tmp_path, [box, Edge.make_line((20, 0, 0), (30, 0, 0))])
    assert compute_metrics(loaded)["edge_count"] > compute_metrics(box.wrapped)["edge_count"]
    assert saved_render_parts(loaded, records([box])) is None
