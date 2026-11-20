"""`agentcad inspect` on a saved show_regions() map: read back without rerunning."""

from __future__ import annotations

import base64
import hashlib
import json
import shlex

import pytest

from agentcad.cli import cli

SKETCH = """\
regions = sketch_regions(Rectangle(60, 30), Pos(-8, 0) * Circle(10),
                         Pos(8, 0) * Circle(10))
show_regions(regions, name="plate")
"""
MAP = "v1_layout_sketch/regions/plate.json"


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


def _inspect(runner, *args):
    result = runner.invoke(cli, ["inspect", *args])
    return result, json.loads(result.stdout)


def _manifest(project):
    return json.loads((project / "agentcad.json").read_text())


def test_saved_map_has_versioned_ids_and_measurements(project):
    listing = json.loads((project / MAP).read_text())
    assert listing["kind"] == "agentcad.region_map"
    assert (listing["version"], listing["label"], listing["map"]) == (1, "layout", "plate")
    assert listing["units"] == "mm"
    assert listing["dimensions"] == {
        "bounds": {"x": [-30.0, 30.0], "y": [-15.0, 15.0]}, "width": 60.0, "height": 30.0,
    }
    assert listing["png_sha256"] == hashlib.sha256(
        (project / listing["png"]).read_bytes()
    ).hexdigest()
    for index, region in enumerate(listing["regions"]):
        assert region["id"] == f"region_v1_plate_{index}"
        assert region["index"] == index
        assert region["perimeter"] > 0
        assert set(region["bounds"]) == {"x", "y"}
        x, y = region["interior"]
        assert region["selector"] == f"regions.at(({x:g}, {y:g}))"


def test_run_response_points_at_inspect(runner, project):
    (project / "sketch.py").write_text(SKETCH)
    result = runner.invoke(
        cli, ["run", "sketch.py", "--label", "again", "--no-view", "--no-daemon"]
    )
    data = json.loads(result.stdout)
    entry = data["regions"]["plate"]
    assert entry["items"][0]["id"] == "region_v2_plate_0"
    assert data["next_actions"][0] == entry["inspect"]
    args = shlex.split(entry["inspect"])
    assert args[:3] == ["agentcad", "inspect", "v2_again_sketch/regions/plate.json"]
    followed = runner.invoke(cli, args[1:])
    assert followed.exit_code == 0, followed.output
    assert json.loads(followed.stdout)["region"]["id"] == "region_v2_plate_0"


def test_summary_is_bounded_and_reads_without_running_anything(runner, project):
    before = _manifest(project)
    # A broken source proves inspect never executes it.
    (project / "sketch.py").write_text("raise RuntimeError('must not run')\n")
    (project / "v1_layout_sketch/script.py").write_text("raise RuntimeError('must not run')\n")

    result, data = _inspect(runner, MAP, "--limit", "2")
    assert result.exit_code == 0, result.output
    assert data["region_map"]["id_prefix"] == "region_v1_plate_"
    assert data["region_map"]["count"] == 4
    assert [r["id"] for r in data["regions"]] == ["region_v1_plate_0", "region_v1_plate_1"]
    assert data["truncation"] == {
        "returned": 2, "total": 4, "next_action": f"agentcad inspect {MAP} --no-limit",
    }
    assert data["next_actions"] == [f"agentcad inspect {MAP} --id region_v1_plate_0 --image"]

    _, full = _inspect(runner, MAP, "--no-limit")
    assert len(full["regions"]) == 4 and "truncation" not in full

    assert _manifest(project) == before
    assert sorted(p.name for p in project.glob("v*")) == ["v1_layout_sketch"]


def test_id_returns_record_and_a_selector_that_builds_that_region(runner, project):
    _, summary = _inspect(runner, MAP)
    lens = min(summary["regions"], key=lambda r: r["area"])
    result, data = _inspect(runner, MAP, "--id", lens["id"])
    assert result.exit_code == 0, result.output
    assert data["region"]["id"] == lens["id"]
    assert data["region"]["area"] == lens["area"]
    selector = data["selector"]
    assert selector["call"] == lens["selector"]
    assert selector["index_call"] == f"regions[{lens['index']}]"
    assert "same sketch" in selector["note"]
    assert "image" not in data

    (project / "part.py").write_text(
        SKETCH + f"show_object(extrude({selector['call']}, amount=5))\n"
    )
    run = runner.invoke(
        cli, ["run", "part.py", "--label", "lens", "--no-view", "--no-daemon",
              "--no-preview", "--no-diff"]
    )
    assert run.exit_code == 0, run.output
    volume = json.loads(run.stdout)["metrics"]["volume"]
    assert volume == pytest.approx(lens["area"] * 5, rel=1e-3)


@pytest.mark.parametrize("entity_id, reason, fragment", [
    ("region_v2_plate_0", "region_id_wrong_map", "version 2, not 1"),
    ("region_v1_lid_0", "region_id_wrong_map", "map 'lid', not 'plate'"),
    ("region_v1_plate_4", "region_id_out_of_range", "it has 4 regions"),
    ("face_v1_3", "invalid_region_id", "not a region ID"),
])
def test_ids_from_another_map_or_version_are_rejected(runner, project, entity_id, reason, fragment):
    result, data = _inspect(runner, MAP, "--id", entity_id)
    assert result.exit_code == 1
    assert data["status"] == "error"
    assert data["reason"] == reason
    assert fragment in data["message"]
    assert "region_v1_plate_0 .. region_v1_plate_3" in data["message"]


def test_image_is_the_saved_full_map(runner, project):
    result, data = _inspect(runner, MAP, "--id", "region_v1_plate_2", "--image")
    assert result.exit_code == 0, result.output
    image = data["image"]
    assert image["scope"] == "full_map"
    assert "labeled 2" in image["note"]
    assert image["png"] == "v1_layout_sketch/regions/plate.png"
    assert image["png_sha256"] == hashlib.sha256((project / image["png"]).read_bytes()).hexdigest()


def test_mcp_and_cli_agree_on_records_and_image_bytes(runner, project):
    from agentcad.mcp import server

    _, cli_data = _inspect(runner, MAP, "--id", "region_v1_plate_1", "--image")
    result = server.inspect(MAP, cwd=str(project), entity_id="region_v1_plate_1", image=True)
    text, attachment = result.content
    mcp_data = json.loads(text.text)
    assert mcp_data["region"] == cli_data["region"]
    assert mcp_data["selector"] == cli_data["selector"]
    png = base64.b64decode(attachment.data)
    assert hashlib.sha256(png).hexdigest() == cli_data["image"]["png_sha256"]
    assert attachment.mimeType == "image/png"


@pytest.mark.parametrize("damage, reason", [
    (lambda png: png.unlink(), "region_image_unavailable"),
    (lambda png: png.write_bytes(png.read_bytes()[:-10] + b"x" * 10), "region_image_changed"),
])
def test_records_stay_usable_when_the_image_is_not(runner, project, damage, reason):
    damage(project / "v1_layout_sketch/regions/plate.png")
    result, data = _inspect(runner, MAP, "--id", "region_v1_plate_0", "--image")
    assert result.exit_code == 1
    assert data["error_kind"] == "image_unavailable"
    assert data["reason"] == reason
    assert data["image"]["status"] == "failed"
    assert data["region"]["id"] == "region_v1_plate_0"
    assert data["selector"]["call"].startswith("regions.at((")
    assert "still usable" in data["suggestion"]

    from agentcad.mcp import server
    mcp_data = server.inspect(MAP, cwd=str(project), entity_id="region_v1_plate_0", image=True)
    assert isinstance(mcp_data, dict) and mcp_data["region"]["id"] == "region_v1_plate_0"


def test_maps_saved_by_a_normal_run_use_its_version(runner, project):
    (project / "part.py").write_text(
        SKETCH + "show_object(extrude(regions.at((25, 10)), amount=4))\n"
    )
    run = runner.invoke(
        cli, ["run", "part.py", "--label", "part", "--no-view", "--no-daemon",
              "--no-preview", "--no-diff"]
    )
    assert run.exit_code == 0, run.output
    result, data = _inspect(runner, "v2_part/regions/plate.json", "--id", "region_v2_plate_0")
    assert result.exit_code == 0, result.output
    assert data["region_map"]["version"] == 2


def test_geometry_options_do_not_apply_to_a_region_map(runner, project):
    result = runner.invoke(cli, ["inspect", MAP, "--ids"])
    assert result.exit_code != 0
    assert "do not apply to a region map" in result.output


def test_other_json_in_a_regions_folder_is_not_mistaken_for_a_map(runner, project):
    other = project / "v1_layout_sketch/regions/notes.json"
    other.write_text(json.dumps({"hello": 1}))
    result, data = _inspect(runner, str(other.relative_to(project)))
    assert result.exit_code == 1
    assert data["reason"] == "not_a_region_map"
