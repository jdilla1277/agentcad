"""`agentcad run` with show_regions(): region maps and sketch-only runs."""

from __future__ import annotations

import json

import pytest

from agentcad.cli import cli

SKETCH = """\
regions = sketch_regions(Rectangle(40, 20), Circle(6), Pos(15, 0) * Circle(6),
                         Line((-20, 0), (20, 0)))
show_regions(regions)
"""
PART = SKETCH + """\
part = extrude([regions[0], regions[2]], amount=5)
show_object(part)
"""
PART_WITHOUT_MAP = """\
regions = sketch_regions(Rectangle(40, 20), Circle(6), Pos(15, 0) * Circle(6),
                         Line((-20, 0), (20, 0)))
part = extrude([regions[0], regions[2]], amount=5)
show_object(part)
"""
TINY = """\
regions = sketch_regions(Rectangle(60, 40), Circle(12), Pos(11.7, 0) * Circle(0.6))
show_regions(regions, name="tiny")
"""


@pytest.fixture
def project(runner, isolated_dir):
    result = runner.invoke(cli, ["init", "--name", "t", "--runtime", "build123d"])
    assert result.exit_code == 0, result.output
    return isolated_dir


def _run(runner, project, source, label, *extra, name="s.py"):
    (project / name).write_text(source)
    result = runner.invoke(
        cli, ["run", name, "--label", label, "--no-view", "--no-daemon", *extra]
    )
    return result, json.loads(result.stdout)


def _manifest(project):
    return json.loads((project / "agentcad.json").read_text())


def test_sketch_only_run_writes_map_without_step_or_current(runner, project):
    result, data = _run(runner, project, SKETCH, "sketch")
    assert result.exit_code == 0, result.output
    assert data["status"] == "success"
    assert data["sketch_only"] is True
    assert data["artifact_created"] is False
    assert data["outputs"]["step"] is None
    assert data["version_recorded"] is True
    assert data["current_advanced"] is False
    assert data["output_type"] == "sketch"
    assert "no STEP" in data["message"]
    assert any("show_object(extrude(" in action for action in data["next_actions"])
    assert data["artifacts"] == {"regions": {"status": "success"}}

    version_dir = project / "v1_sketch_sketch"
    entry = data["regions"]["regions"]
    assert entry["png"] == "v1_sketch_sketch/regions/regions.png"
    assert entry["png"] in data["hint"]
    assert (project / entry["png"]).is_file()
    listing = json.loads((project / entry["json"]).read_text())
    assert listing["count"] == entry["count"] == 7
    assert [item["index"] for item in listing["regions"]] == list(range(7))
    first = entry["items"][0]
    assert set(first) == {
        "index", "area", "center", "center_global", "interior", "interior_global",
        "holes", "label", "color", "tiny",
    }
    assert entry["plane"]["z_dir"] == [0.0, 0.0, 1.0]

    # No deliverable artifacts for a sketch.
    for name in ("output.step", "output.glb", "viewer.html", "preview.png"):
        assert not (version_dir / name).exists()
    manifest = _manifest(project)
    assert manifest.get("current") is None
    assert manifest["versions"][0]["status"] == "sketch"
    meta = json.loads((version_dir / "meta.json").read_text())
    assert meta["status"] == "sketch" and meta["sketch_only"] is True
    assert "step" not in meta["outputs"]
    assert meta["artifacts"]["regions"]["status"] == "success"
    assert meta["regions"]["regions"]["count"] == 7


def test_history_commands_and_later_runs_ignore_sketch_versions(runner, project):
    _run(runner, project, SKETCH, "sketch")
    result, first = _run(runner, project, PART, "part")
    assert result.exit_code == 0, result.output
    assert first["artifacts"]["diff"]["status"] == "skipped"  # nothing to diff against
    assert _manifest(project)["current"] == "part"

    _run(runner, project, SKETCH, "sketch2")  # sketch between two builds
    assert _manifest(project)["current"] == "part"
    result, second = _run(runner, project, PART, "part2")
    assert result.exit_code == 0, result.output
    assert second["diff"]["against"] == "part"

    context = json.loads(runner.invoke(cli, ["context"]).stdout)
    assert context["current"] == "part2"
    assert context["recovery"]["status"] == "clean"
    assert [v["status"] for v in context["versions"]] == ["sketch", "success", "sketch", "success"]

    diff = runner.invoke(cli, ["diff", "1", "2", "--no-daemon"])
    assert diff.exit_code == 0, diff.output
    assert json.loads(diff.stdout)["changes"]["status"] == {"from": "sketch", "to": "success"}

    parts = json.loads(runner.invoke(cli, ["parts", "list", "1"]).stdout)
    assert parts["version_status"] == "sketch" and parts["parts"] == []


def test_regions_do_not_change_the_delivered_step(runner, project):
    result, with_map = _run(runner, project, PART, "with_map", "--no-preview", "--no-diff")
    assert result.exit_code == 0, result.output
    result, without = _run(
        runner, project, PART_WITHOUT_MAP, "without", "--no-preview", "--no-diff"
    )
    assert result.exit_code == 0, result.output
    assert with_map["metrics"]["face_count"] == without["metrics"]["face_count"]
    assert with_map["metrics"]["volume"] == pytest.approx(without["metrics"]["volume"])
    assert with_map["output_type"] == "single_part"
    assert "parts" in with_map and len(with_map["parts"]) == 1
    assert with_map["artifacts"]["regions"]["status"] == "success"
    assert "regions" not in without["artifacts"]
    assert "regions" not in without
    assert (project / with_map["regions"]["regions"]["png"]).is_file()
    meta = json.loads((project / "v1_with_map" / "meta.json").read_text())
    assert meta["regions"]["regions"]["count"] == 7


def test_multiple_maps_and_duplicate_name(runner, project):
    source = SKETCH + "show_regions(regions[:3], name='top_row')\n"
    result, data = _run(runner, project, source, "two")
    assert result.exit_code == 0, result.output
    assert set(data["regions"]) == {"regions", "top_row"}
    assert data["regions"]["top_row"]["count"] == 3

    result, data = _run(runner, project, SKETCH + "show_regions(regions)\n", "dup")
    assert result.exit_code == 1
    assert data["status"] == "failed"
    assert "already used" in data["error"]


def test_tiny_region_gets_leader_label_and_is_listed(runner, project):
    result, data = _run(runner, project, TINY, "tiny")
    assert result.exit_code == 0, result.output
    entry = data["regions"]["tiny"]
    tiny = min(entry["items"], key=lambda item: item["area"])
    assert tiny["label"] == "leader"
    largest = max(entry["items"], key=lambda item: item["area"])
    assert largest["label"] == "inside"
    listing = json.loads((project / entry["json"]).read_text())
    assert tiny["index"] in [item["index"] for item in listing["regions"]]


def test_map_render_failure_does_not_fail_the_run(runner, project, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("no renderer")

    monkeypatch.setattr("agentcad.region_map.render_region_map", broken)
    result, data = _run(runner, project, PART, "broken", "--no-preview", "--no-diff")
    assert result.exit_code == 0, result.output
    assert data["status"] == "success"
    assert data["artifacts"]["regions"]["status"] == "failed"
    assert "no renderer" in data["artifacts"]["regions"]["message"]
    entry = data["regions"]["regions"]
    assert entry["png"] is None
    assert (project / entry["json"]).is_file()
    assert "label" not in entry["items"][0]
    assert any("region map" in warning for warning in data["warnings"])
    assert (project / "v1_broken" / "output.step").is_file()

    result, data = _run(runner, project, SKETCH, "broken_sketch")
    assert result.exit_code == 0, result.output
    assert data["sketch_only"] is True
    assert data["artifacts"]["regions"]["status"] == "failed"
    assert "hint" not in data


def test_inline_items_are_capped_with_a_pointer_to_the_json(runner, project):
    source = (
        "squares = [Pos(x * 6, y * 6) * Rectangle(4, 4) for x in range(8) for y in range(8)]\n"
        "show_regions(sketch_regions(*squares), name='grid')\n"
    )
    result, data = _run(runner, project, source, "grid")
    assert result.exit_code == 0, result.output
    entry = data["regions"]["grid"]
    assert entry["count"] == 64
    assert len(entry["items"]) == 50
    assert entry["items_truncated"] is True
    assert entry["json"] in entry["note"]
    assert len(json.loads((project / entry["json"]).read_text())["regions"]) == 64


def test_dry_run_sketch_only_writes_nothing(runner, project):
    (project / "s.py").write_text(SKETCH)
    result = runner.invoke(cli, ["run", "s.py", "--dry-run", "--no-daemon"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["sketch_only"] is True
    assert data["regions"]["regions"]["count"] == 7
    assert data["regions"]["regions"]["png"] is None
    assert _manifest(project)["versions"] == []
    assert not list(project.glob("v*"))


def test_script_without_any_output_call_is_unchanged(runner, project):
    (project / "s.py").write_text("regions = sketch_regions(Circle(5))\n")
    result = runner.invoke(cli, ["run", "s.py", "--label", "x", "--no-daemon"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "validation_error"
    check = data["checks"][0]
    assert check["check"] == "show_object_missing"
    assert "show_regions" not in check["message"]


def test_sketch_region_errors_surface_as_failed_runs(runner, project):
    source = "show_regions(sketch_regions(Circle(5), Plane.XZ * Circle(5)))\n"
    result, data = _run(runner, project, source, "bad")
    assert result.exit_code == 1
    assert data["status"] == "failed"
    assert "not coplanar" in data["error"]


SPECKS = """\
regions = sketch_regions(Rectangle(60, 40), Pos(-20, 12) * Circle(0.3),
                         Pos(-19.5, 12) * Circle(0.3))
show_regions(regions, name="specks")
"""


def test_tiny_regions_are_flagged_with_a_window_hint_that_works(runner, project):
    import re

    result, data = _run(runner, project, SPECKS, "specks")
    assert result.exit_code == 0, result.output
    entry = data["regions"]["specks"]
    tiny = [item for item in entry["items"] if item["tiny"]]
    assert len(tiny) == 1 and tiny[0]["label"] == "leader"
    assert f"Region {tiny[0]['index']} is only a few pixels" in entry["hint"]
    assert "window=" in entry["hint"] and entry["hint"] in data["hint"]
    assert "regions.at(" in data["hint"]
    listing = json.loads((project / entry["json"]).read_text())
    assert listing["hint"] == entry["hint"]

    # The suggested call, pasted into the script, zooms onto the specks.
    call = re.search(r"show_regions\(.*?\)\)\)", entry["hint"]).group(0)
    result, data = _run(runner, project, SPECKS + call + "\n", "zoom")
    assert result.exit_code == 0, result.output
    zoom = data["regions"]["specks_zoom"]
    assert zoom["window"][0][0] < -20 < zoom["window"][1][0]
    by_index = {item["index"]: item for item in zoom["items"]}
    assert by_index[tiny[0]["index"]]["tiny"] is False
    assert by_index[tiny[0]["index"]]["label"] in {"inside", "leader"}
    # Every region reaches into this window (the plate surrounds the specks).
    assert {item["label"] for item in zoom["items"]} <= {"inside", "leader"}
    assert "hint" not in zoom


def test_window_map_marks_regions_outside_it(runner, project):
    source = SKETCH.replace(
        "show_regions(regions)", "show_regions(regions, window=((-8, -8), (8, 8)))"
    )
    result, data = _run(runner, project, source, "win")
    assert result.exit_code == 0, result.output
    entry = data["regions"]["regions"]
    assert entry["window"] == [[-8.0, -8.0], [8.0, 8.0]]
    labels = {item["index"]: item["label"] for item in entry["items"]}
    assert labels[3] == "outside_window"  # the sliver right of the plate (x > 20)
    assert labels[1] == "inside" and labels[4] == "inside"  # the left circle halves

    result = runner.invoke(cli, ["run", "s.py", "--dry-run", "--no-daemon"])
    assert json.loads(result.stdout)["regions"]["regions"]["window"] == [[-8.0, -8.0], [8.0, 8.0]]


def test_items_carry_an_interior_point_usable_with_at(runner, project):
    source = """\
regions = sketch_regions(Polyline((-10, 0), (10, 0), (10, 10), (6, 10), (6, 3),
                                  (-6, 3), (-6, 10), (-10, 10), close=True))
show_regions(regions, name="u")
"""
    result, data = _run(runner, project, source, "u")
    assert result.exit_code == 0, result.output
    [item] = data["regions"]["u"]["items"]
    cx, cy = item["center"]
    assert 3 < cy < 10 and -6 < cx < 6  # the centroid is in the U's gap
    x, y = item["interior"]
    assert (0 <= y <= 3) or (abs(x) >= 6 and 0 <= y <= 10)

    picked = source.replace(
        'show_regions(regions, name="u")',
        f"show_object(extrude(regions.at(({x}, {y})), amount=2))",
    )
    result, data = _run(runner, project, picked, "picked", "--no-preview", "--no-diff")
    assert result.exit_code == 0, result.output
    assert data["status"] == "success"


def test_map_labels_are_placed_at_the_reported_interior_points(runner, project, monkeypatch):
    from agentcad import region_map

    calls = []
    real = region_map.render_region_map

    def spy(*args, **kwargs):
        placements = real(*args, **kwargs)
        calls.append((kwargs.get("interiors"), placements))
        return placements

    monkeypatch.setattr(region_map, "render_region_map", spy)
    result, data = _run(runner, project, SKETCH, "sketch")
    assert result.exit_code == 0, result.output
    [(interiors, placements)] = calls
    items = data["regions"]["regions"]["items"]
    assert interiors == [item["interior"] for item in items]
    for placement in placements:
        if placement["label"] == "inside":
            x, y, w, h = placement["box"]
            tx, ty = placement["target"]
            assert abs(x + w / 2 - tx) <= 1 and abs(y + h / 2 - ty) <= 1
