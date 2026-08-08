import json
from pathlib import Path

import pytest

from agentcad.cli import cli
from agentcad.explode import explode_offsets, parse_explode_factor


TWO_PART_SCRIPT = """\
import cadquery as cq
base = cq.Workplane("XY").box(20, 20, 5)
top = cq.Workplane("XY").box(10, 10, 5).translate((0, 0, 5))
show_object(base, name="base", options={"color": "gray"})
show_object(top, name="top", options={"color": "red"})
"""

SINGLE_BOX_SCRIPT = """\
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10)
show_object(result)
"""


def _init_project(runner):
    runner.invoke(cli, ["init", "--name", "explode_test", "--runtime", "cadquery"])


def _run_script(runner, isolated_dir, script, label):
    path = isolated_dir / "script.py"
    path.write_text(script)
    result = runner.invoke(
        cli, ["run", "script.py", "--output", label, "--no-preview", "--no-view"],
    )
    assert json.loads(result.stdout.splitlines()[-1])["status"] == "success", result.output


# ---- pure factor / offset math ----

def test_parse_explode_factor_accepts_percent_and_float():
    assert parse_explode_factor("50%") == 0.5
    assert parse_explode_factor("100%") == 1.0
    assert parse_explode_factor("0.5") == 0.5
    assert parse_explode_factor(1.5) == 1.5
    assert parse_explode_factor("0") == 0.0


def test_parse_explode_factor_rejects_bad_values():
    with pytest.raises(ValueError):
        parse_explode_factor("huge")
    with pytest.raises(ValueError):
        parse_explode_factor("-10%")
    with pytest.raises(ValueError):
        parse_explode_factor("900%")


def test_explode_offsets_radial_scaling():
    centers = [(0.0, 0.0, -2.0), (0.0, 0.0, 6.0), (4.0, 0.0, 2.0)]
    offsets = explode_offsets(centers, (0.0, 0.0, 2.0), 0.5)
    assert offsets[0] == (0.0, 0.0, -2.0)
    assert offsets[1] == (0.0, 0.0, 2.0)
    assert offsets[2] == (2.0, 0.0, 0.0)


def test_explode_offsets_zero_for_concentric_part():
    offsets = explode_offsets([(1.0, 1.0, 1.0)], (1.0, 1.0, 1.0), 1.0)
    assert offsets == [(0.0, 0.0, 0.0)]


# ---- CLI: version references ----

def test_explode_version_renders_and_viewer(runner, isolated_dir):
    _init_project(runner)
    _run_script(runner, isolated_dir, TWO_PART_SCRIPT, "assembly")

    result = runner.invoke(
        cli, ["explode", "current", "--factor", "50%", "--no-open"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout.splitlines()[-1])

    assert data["command"] == "explode"
    assert data["status"] == "success"
    assert data["factor"] == 0.5
    assert data["percent"] == 50
    assert data["grouping"] == "parts"
    assert data["part_count"] == 2
    assert [p["id"] for p in data["parts"]] == ["base", "top"]

    # base sits below the assembly center, top above: offsets point apart in z
    by_id = {p["id"]: p for p in data["parts"]}
    assert by_id["base"]["offset"][2] < 0
    assert by_id["top"]["offset"][2] > 0
    assert by_id["base"]["moved"] and by_id["top"]["moved"]

    # assembled bounding boxes let agents answer contact questions from JSON:
    # base top face (z=2.5) meets top part bottom face (z=2.5)
    assert by_id["base"]["bounding_box"]["z"] == [-2.5, 2.5]
    assert by_id["top"]["bounding_box"]["z"] == [2.5, 7.5]

    for png in data["renders"].values():
        assert Path(png).exists()
    assert Path(data["viewer"]).exists()

    # renders recorded in meta.json like the render command's output
    meta = json.loads((isolated_dir / "v1_assembly" / "meta.json").read_text())
    assert any(k.startswith("exploded_50_") for k in meta["renders"])

    # viewer HTML seeds the explode slider state
    html = Path(data["viewer"]).read_text()
    assert '"explode": 0.5' in html


def test_explode_single_solid_is_actionable_error(runner, isolated_dir):
    _init_project(runner)
    _run_script(runner, isolated_dir, SINGLE_BOX_SCRIPT, "box")

    result = runner.invoke(cli, ["explode", "current", "--no-open"])
    assert result.exit_code == 1
    data = json.loads(result.stdout.splitlines()[-1])
    assert data["status"] == "error"
    assert "single solid" in data["message"]
    assert "suggestion" in data


def test_explode_unknown_version_errors(runner, isolated_dir):
    _init_project(runner)
    result = runner.invoke(cli, ["explode", "v9", "--no-open"])
    assert result.exit_code == 1
    data = json.loads(result.stdout.splitlines()[-1])
    assert data["status"] == "error"
    assert "not found" in data["message"]


def test_explode_bad_factor_errors(runner, isolated_dir):
    _init_project(runner)
    result = runner.invoke(
        cli, ["explode", "current", "--factor", "huge", "--no-open"],
    )
    assert result.exit_code == 1
    data = json.loads(result.stdout.splitlines()[-1])
    assert data["status"] == "error"
    assert "50%" in data["message"]


# ---- CLI: standalone STEP files ----

def test_explode_standalone_step_uses_solids(runner, isolated_dir):
    from cadquery import Compound, Workplane, exporters

    a = Workplane("XY").box(10, 10, 10)
    b = Workplane("XY").box(10, 10, 10).translate((20, 0, 0))
    compound = Compound.makeCompound([a.val(), b.val()])
    step_path = isolated_dir / "pair.step"
    exporters.export(Workplane(obj=compound), str(step_path))

    result = runner.invoke(
        cli, ["explode", str(step_path), "--factor", "100%", "--no-open"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout.splitlines()[-1])
    assert data["status"] == "success"
    assert data["grouping"] == "solids"
    assert [p["id"] for p in data["parts"]] == ["part_1", "part_2"]
    # 100%: each part's offset equals its distance from the assembly center
    by_id = {p["id"]: p for p in data["parts"]}
    assert by_id["part_1"]["offset"][0] == pytest.approx(-10.0, abs=0.01)
    assert by_id["part_2"]["offset"][0] == pytest.approx(10.0, abs=0.01)
    assert Path(data["viewer"]).exists()
    for png in data["renders"].values():
        assert Path(png).exists()


def test_explode_missing_step_file_errors(runner, isolated_dir):
    result = runner.invoke(cli, ["explode", "missing.step", "--no-open"])
    assert result.exit_code == 1
    data = json.loads(result.stdout.splitlines()[-1])
    assert data["status"] == "error"


# ---- parts view --explode integration ----

def test_parts_view_explode_seeds_viewer(runner, isolated_dir):
    version_dir = isolated_dir / "v1_assembly"
    version_dir.mkdir()
    (version_dir / "output.glb").write_bytes(b"glb-test")
    (version_dir / "meta.json").write_text(json.dumps({
        "version": 1,
        "label": "assembly",
        "status": "success",
        "outputs": {"step": "v1_assembly/output.step"},
        "viewer_glb": "v1_assembly/output.glb",
        "parts": [
            {"id": "base", "id_source": "name", "name": "base"},
            {"id": "top", "id_source": "name", "name": "top"},
        ],
    }))
    (isolated_dir / "agentcad.json").write_text(json.dumps({
        "name": "explode-test",
        "current": "assembly",
        "versions": [{
            "version": 1,
            "label": "assembly",
            "status": "success",
            "path": "v1_assembly/",
        }],
    }))

    result = runner.invoke(
        cli, ["parts", "view", "v1", "--explode", "75%", "--no-open"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout.splitlines()[-1])
    assert data["status"] == "success"
    assert data["part_review"]["explode"] == 0.75

    html = Path(data["review_viewer"]).read_text()
    assert '"explode": 0.75' in html


def test_parts_view_bad_explode_errors(runner, isolated_dir):
    (isolated_dir / "agentcad.json").write_text(json.dumps({
        "name": "explode-test",
        "current": None,
        "versions": [],
    }))
    result = runner.invoke(
        cli, ["parts", "view", "v1", "--explode", "nope", "--no-open"],
    )
    assert result.exit_code == 1
    data = json.loads(result.stdout.splitlines()[-1])
    assert data["status"] == "error"
