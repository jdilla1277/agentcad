"""End-to-end `agentcad run` through the build123d runtime.

Mirrors the integration surface covered by tests/test_run.py for the
CadQuery runtime. Goal: every user-facing `run` behavior the CadQuery
path exercises — version directory layout, metadata, preview, params,
renders, exports, failures, metrics — also works on build123d. Flag
combinations matter more than unit-level assertions; this file is
coarse-grained on purpose.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentcad.cli import cli


SIMPLE = "from build123d import Box\nshow_object(Box(10, 10, 10))\n"
MULTI_SHOW = """\
from build123d import Box, Cylinder
box = Box(10, 10, 10)
show_object(box)
cyl = Cylinder(radius=5, height=10).translate((0, 0, 25))
show_object(cyl)
"""
NAMED_PARTS = """\
from build123d import Box, Cylinder
deck = Box(10, 10, 2)
pin = Cylinder(radius=1, height=5).translate((3, 0, 0))
arm = Box(8, 2, 1).translate((0, 5, 0))
show_object(deck, name="deck", options={"color": "gray"})
show_object(pin, name="pin", options={"color": "blue"})
show_object(arm, name="arm", options={"color": "red"})
"""
PARTIAL_NAMED_PARTS = """\
from build123d import Box, Cylinder
deck = Box(10, 10, 2)
pin = Cylinder(radius=1, height=5).translate((3, 0, 0))
arm = Box(8, 2, 1).translate((0, 5, 0))
show_object(deck, name="deck")
show_object(pin)
show_object(arm, name="arm")
"""
DUPLICATE_NAMES = """\
from build123d import Box
a = Box(10, 10, 2)
b = Box(5, 5, 2).translate((20, 0, 0))
show_object(a, name="wheel")
show_object(b, name="wheel")
"""
PARAMETRIC_FLOAT = """\
from build123d import Box
length = 50.0
width = 20.0
height = 10.0
show_object(Box(length, width, height))
"""
PARAMS_INT = """\
from build123d import Box
count = 5
show_object(Box(count, count, count))
"""
PARAMS_STRING = """\
from build123d import Box
label = "default"
show_object(Box(10, 10, 10))
"""
PARAMS_BOOL = """\
from build123d import Box
smooth = False
show_object(Box(10, 10, 10))
"""
PARAMETRIC = """\
from build123d import Box
length = 20
width = 10
thickness = 5
show_object(Box(length, width, thickness))
"""
MULTI = """\
from build123d import Box, Sphere, Compound
a = Box(10, 10, 10)
b = Sphere(6).translate((20, 0, 0))
show_object(Compound(children=[a, b]))
"""
BROKEN = """\
from build123d import Box
show_object(Box(0, 0, 0))
"""


def _init(runner, isolated_dir):
    r = runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
    assert r.exit_code == 0, r.output


def _write(isolated_dir, name, body):
    (isolated_dir / name).write_text(body)


def _run(runner, *args):
    return runner.invoke(cli, ["run", *args])


# ---------- version directory + metadata ----------

class TestVersionDirectory:
    def test_creates_versioned_output_step(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "first")
        assert r.exit_code == 0, r.output
        assert (isolated_dir / "v1_first" / "output.step").exists()

    def test_copies_script_into_version_dir(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "copy")
        copied = isolated_dir / "v1_copy" / "script.py"
        assert copied.exists()
        assert copied.read_text() == SIMPLE

    def test_meta_json_has_runtime_and_metrics(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "m")
        meta = json.loads((isolated_dir / "v1_m" / "meta.json").read_text())
        assert meta["status"] == "success"
        assert meta["runtime"] == "build123d"
        assert meta["metrics"]["volume"] == 1000.0
        assert meta["metrics"]["is_valid"] is True

    def test_second_run_increments_version(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "one")
        _run(runner, "s.py", "--output", "two")
        assert (isolated_dir / "v1_one").is_dir()
        assert (isolated_dir / "v2_two").is_dir()


# ---------- preview (default on) ----------

class TestPreview:
    def test_preview_on_by_default(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "p")
        assert (isolated_dir / "v1_p" / "preview.png").exists()

    def test_no_preview_suppresses(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "np", "--no-preview")
        assert not (isolated_dir / "v1_np" / "preview.png").exists()
        # Parity with cq: --no-preview also drops the preview key from JSON + meta.
        parsed = json.loads(r.stdout)
        assert "preview" not in parsed
        meta = json.loads((isolated_dir / "v1_np" / "meta.json").read_text())
        assert "preview" not in meta

    # ---- parity ports from tests/test_run.py ----

    def test_preview_json_response(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "label", "--preview")
        parsed = json.loads(r.stdout)
        assert "preview" in parsed
        assert parsed["preview"] == "v1_label/preview.png"

    def test_preview_meta_json(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "label", "--preview")
        meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
        assert "preview" in meta
        assert meta["preview"] == "v1_label/preview.png"

    def test_preview_not_in_renders(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "label", "--preview")
        meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
        assert "renders" not in meta

    def test_preview_with_render_coexist(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "label", "--preview", "--render", "iso")
        assert r.exit_code == 0
        assert (isolated_dir / "v1_label" / "preview.png").exists()
        assert (isolated_dir / "v1_label" / "renders" / "iso.png").exists()

    def test_dry_run_skips_preview(self, runner, isolated_dir):
        """--dry-run produces no disk artifacts, including a preview."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "label", "--dry-run")
        parsed = json.loads(r.stdout)
        assert parsed["status"] == "success"
        assert "preview" not in parsed
        assert not (isolated_dir / "v1_label").exists()

    def test_preview_is_4view_composite_1024(self, runner, isolated_dir):
        """Preview is a 2x2 composite of front/right/top/iso, 512px per quadrant."""
        import struct as _struct
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "label", "--preview")
        png = isolated_dir / "v1_label" / "preview.png"
        data = png.read_bytes()
        width, height = _struct.unpack(">II", data[16:24])
        # 2 panels wide × 512px = 1024, plus label bars stacked (2 × (512+22) = 1068).
        assert width == 1024
        assert height == 1068

    def test_default_does_not_auto_render_preview_gif(self, runner, isolated_dir):
        """Turntable GIFs are on-demand via the viewer's Export GIF button —
        they are not auto-rendered on every run. b3d twin of the cq test."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "v1")
        assert r.exit_code == 0, r.output

        parsed = json.loads(r.stdout)
        assert "preview_gif" not in parsed
        assert not (isolated_dir / "v1" / "preview.gif").exists()

        meta = json.loads((isolated_dir / "v1" / "meta.json").read_text())
        assert "preview_gif" not in meta

    def test_no_preview_still_writes_viewer_and_glb(self, runner, isolated_dir):
        """--no-preview only skips the 4-view composite PNG. viewer.html and
        output.glb still ship because they are cheap and the agent needs them
        for the comparison viewer to load. b3d twin of the cq test."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "label", "--no-preview")
        assert r.exit_code == 0, r.output
        parsed = json.loads(r.stdout)
        assert parsed["viewer"] == "v1_label/viewer.html"
        assert (isolated_dir / "v1_label" / "viewer.html").exists()
        assert (isolated_dir / "v1_label" / "output.glb").exists()
        meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
        assert meta["viewer"] == "v1_label/viewer.html"

    def test_no_preview_second_run_still_writes_diff(self, runner, isolated_dir):
        """Auto-diff runs regardless of --preview. b3d twin of the cq test."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "first", "--no-preview")
        _write(isolated_dir, "s.py", "show_object(Box(20, 20, 20))\n")
        r = _run(runner, "s.py", "--output", "second", "--no-preview")
        assert r.exit_code == 0, r.output
        parsed = json.loads(r.stdout)
        assert "diff" in parsed
        assert parsed["diff"]["against"] == "first"
        assert (isolated_dir / "v2_second" / "diff_side.png").exists()
        assert (isolated_dir / "v2_second" / "diff_overlay.png").exists()

    def test_no_preview_second_run_viewer_includes_prior(self, runner, isolated_dir):
        """Viewer on run #2 embeds the prior version's GLB so side-by-side
        comparison works even when both runs used --no-preview. b3d twin."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "first", "--no-preview")
        _write(isolated_dir, "s.py", "show_object(Box(20, 20, 20))\n")
        _run(runner, "s.py", "--output", "second", "--no-preview")
        viewer_html = (isolated_dir / "v2_second" / "viewer.html").read_text()
        assert 'DEFAULT_MODE = "side-by-side"' in viewer_html


# ---------- params ----------

class TestParams:
    def test_defaults_produce_expected_dims(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", PARAMETRIC)
        r = _run(runner, "s.py", "--output", "default", "--no-preview", "--dry-run")
        parsed = json.loads(r.stdout)
        assert parsed["metrics"]["volume"] == 20 * 10 * 5

    def test_override_changes_geometry(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", PARAMETRIC)
        r = _run(runner, "s.py", "--output", "big", "--no-preview", "--dry-run",
                  "--params", "length=40,thickness=8")
        parsed = json.loads(r.stdout)
        assert parsed["metrics"]["volume"] == 40 * 10 * 8

    def test_unknown_param_fails_fast(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", PARAMETRIC)
        r = _run(runner, "s.py", "--output", "bad", "--params", "nope=1")
        parsed = json.loads(r.stdout)
        assert parsed["status"] == "error"
        assert "nope" in parsed["message"]
        # Should list available params to help the agent (parity with cq).
        assert "length" in parsed["message"]
        # No version consumed
        assert not any(p.name.startswith("v1_") for p in isolated_dir.iterdir())

    # ---- parity ports from tests/test_run.py::test_run_params_* ----

    def test_override_dims_grow(self, runner, isolated_dir):
        """Override changes the bounding box. cq parity: a different angle on
        override-correctness than test_override_changes_geometry above (which
        asserts on volume); this one targets the JSON dimensions key directly."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", PARAMETRIC_FLOAT)
        r1 = _run(runner, "s.py", "--output", "default")
        _write(isolated_dir, "s.py", PARAMETRIC_FLOAT)
        r2 = _run(runner, "s.py", "--output", "big", "--params", "length=100")
        m1 = json.loads(r1.stdout)["metrics"]
        m2 = json.loads(r2.stdout)["metrics"]
        assert m2["dimensions"]["x"] > m1["dimensions"]["x"]

    def test_params_in_json_response(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", PARAMETRIC_FLOAT)
        r = _run(runner, "s.py", "--output", "p", "--params", "length=100")
        parsed = json.loads(r.stdout)
        assert parsed["status"] == "success"
        assert "params" in parsed
        assert parsed["params"]["length"] == 100.0

    def test_params_in_meta_json(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", PARAMETRIC_FLOAT)
        _run(runner, "s.py", "--output", "p", "--params", "length=100")
        meta = json.loads((isolated_dir / "v1_p" / "meta.json").read_text())
        assert "params" in meta
        assert meta["params"]["length"] == 100.0

    def test_params_multiple_values(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", PARAMETRIC_FLOAT)
        r = _run(runner, "s.py", "--output", "p", "--params", "length=60,width=30")
        parsed = json.loads(r.stdout)
        assert parsed["status"] == "success"
        assert parsed["params"]["length"] == 60.0
        assert parsed["params"]["width"] == 30.0

    def test_params_int_preserved(self, runner, isolated_dir):
        """Integer values stay int, not coerced to float."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", PARAMS_INT)
        r = _run(runner, "s.py", "--output", "p", "--params", "count=10")
        parsed = json.loads(r.stdout)
        assert parsed["params"]["count"] == 10
        assert isinstance(parsed["params"]["count"], int)

    def test_params_float_coercion(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", PARAMETRIC_FLOAT)
        r = _run(runner, "s.py", "--output", "p", "--params", "length=0.5")
        parsed = json.loads(r.stdout)
        assert parsed["params"]["length"] == 0.5
        assert isinstance(parsed["params"]["length"], float)

    def test_params_string_value(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", PARAMS_STRING)
        r = _run(runner, "s.py", "--output", "p", "--params", "label=test")
        parsed = json.loads(r.stdout)
        assert parsed["params"]["label"] == "test"

    def test_params_bool_true(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", PARAMS_BOOL)
        r = _run(runner, "s.py", "--output", "p", "--params", "smooth=true")
        parsed = json.loads(r.stdout)
        assert parsed["params"]["smooth"] is True

    def test_params_bad_format_error(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", PARAMETRIC_FLOAT)
        r = _run(runner, "s.py", "--output", "p", "--params", "badformat")
        assert r.exit_code == 1
        parsed = json.loads(r.stdout)
        assert parsed["status"] == "error"

    def test_without_params_no_params_key(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "v1")
        parsed = json.loads(r.stdout)
        assert "params" not in parsed
        meta = json.loads((isolated_dir / "v1" / "meta.json").read_text())
        assert "params" not in meta


# ---------- render ----------

class TestRender:
    def test_iso_render_produces_png(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "r", "--render", "iso", "--no-preview")
        assert (isolated_dir / "v1_r" / "renders" / "iso.png").exists()

    def test_multiple_named_views(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "r2", "--render", "front,top,iso",
              "--no-preview")
        renders = isolated_dir / "v1_r2" / "renders"
        for view in ("front", "top", "iso"):
            assert (renders / f"{view}.png").exists()

    def test_render_metadata_in_json(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "r3", "--render", "iso",
                  "--no-preview")
        parsed = json.loads(r.stdout)
        assert "renders" in parsed
        assert "iso" in parsed["renders"]

    # ---- parity ports from tests/test_run.py ----

    def test_render_custom_angle(self, runner, isolated_dir):
        """`--render 45:30` produces a custom-angle render."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "label", "--render", "45:30", "--no-preview")
        png = isolated_dir / "v1_label" / "renders" / "45_30.png"
        assert png.exists()
        assert png.read_bytes()[:4] == b"\x89PNG"

    def test_render_mixed_named_and_angle(self, runner, isolated_dir):
        """`--render front,45:30` renders both."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "label", "--render", "front,45:30",
             "--no-preview")
        renders = isolated_dir / "v1_label" / "renders"
        assert (renders / "front.png").exists()
        assert (renders / "45_30.png").exists()

    def test_render_all(self, runner, isolated_dir):
        """`--render all` produces front, right, top, iso."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "label", "--render", "all", "--no-preview")
        renders = isolated_dir / "v1_label" / "renders"
        for name in ("front", "right", "top", "iso"):
            assert (renders / f"{name}.png").exists()

    def test_render_meta_json(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "label", "--render", "iso", "--no-preview")
        meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
        assert "renders" in meta
        assert "iso" in meta["renders"]
        assert meta["renders"]["iso"] == "v1_label/renders/iso.png"

    def test_without_render_no_renders_key(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "label", "--no-preview")
        parsed = json.loads(r.stdout)
        assert "renders" not in parsed
        meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
        assert "renders" not in meta


# ---------- export ----------

class TestExportFlag:
    def test_stl_export(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "e", "--export", "stl", "--no-preview")
        parsed = json.loads(r.stdout)
        assert "stl" in parsed["outputs"]
        assert (isolated_dir / "v1_e" / "output.stl").exists()

    def test_glb_export(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "g", "--export", "glb", "--no-preview")
        parsed = json.loads(r.stdout)
        assert "glb" in parsed["outputs"]
        assert (isolated_dir / "v1_g" / "output.glb").exists()

    def test_obj_export(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "o", "--export", "obj", "--no-preview")
        assert (isolated_dir / "v1_o" / "output.obj").exists()

    def test_multiple_formats(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "multi", "--export", "stl,glb,obj",
                  "--no-preview")
        parsed = json.loads(r.stdout)
        for fmt in ("stl", "glb", "obj"):
            assert fmt in parsed["outputs"]

    # ---- parity ports from tests/test_run.py ----

    def test_export_and_render(self, runner, isolated_dir):
        """`--export glb --render iso` produces both artifacts."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "label", "--export", "glb",
             "--render", "iso", "--no-preview")
        assert (isolated_dir / "v1_label" / "output.glb").exists()
        assert (isolated_dir / "v1_label" / "renders" / "iso.png").exists()

    def test_export_meta_json(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "label", "--export", "stl,glb",
             "--no-preview")
        meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
        assert meta["outputs"]["stl"] == "v1_label/output.stl"
        assert meta["outputs"]["glb"] == "v1_label/output.glb"
        assert meta["outputs"]["step"] == "v1_label/output.step"

    def test_export_json_response_paths(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "label", "--export", "stl,glb",
                 "--no-preview")
        parsed = json.loads(r.stdout)
        assert parsed["outputs"]["stl"] == "v1_label/output.stl"
        assert parsed["outputs"]["glb"] == "v1_label/output.glb"
        assert parsed["outputs"]["step"] == "v1_label/output.step"

    def test_without_export_no_extra_outputs(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "label", "--no-preview")
        parsed = json.loads(r.stdout)
        assert "stl" not in parsed["outputs"]
        assert "glb" not in parsed["outputs"]
        meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
        assert "stl" not in meta["outputs"]
        assert "glb" not in meta["outputs"]


# ---------- dry-run ----------

class TestDryRun:
    def test_no_disk_artifacts(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "dr", "--dry-run")
        assert r.exit_code == 0
        assert not any(p.name.startswith("v1_") for p in isolated_dir.iterdir())

    def test_returns_metrics(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "dr", "--dry-run")
        parsed = json.loads(r.stdout)
        assert parsed["status"] == "success"
        assert parsed["runtime"] == "build123d"
        assert parsed["metrics"]["volume"] == 1000.0


# ---------- failures ----------

class TestFailures:
    def test_missing_script_errors(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        r = _run(runner, "nope.py", "--output", "x")
        parsed = json.loads(r.stdout)
        assert parsed["status"] == "error"
        assert "not found" in parsed["message"].lower()

    def test_broken_geometry_creates_failed_version(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", BROKEN)
        r = _run(runner, "s.py", "--output", "boom")
        parsed = json.loads(r.stdout)
        assert parsed["status"] == "failed"
        assert parsed["runtime"] == "build123d"
        assert (isolated_dir / "v1_boom_failed").is_dir()

    def test_no_show_object_fails_validation(self, runner, isolated_dir):
        """After the validate() parity fix, missing show_object is caught by
        validation — not as failed/error. No version consumed, no disk."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", "from build123d import Box\nx = Box(1, 1, 1)\n")
        r = _run(runner, "s.py", "--output", "nos")
        parsed = json.loads(r.stdout)
        assert parsed["status"] == "validation_error"
        assert any(c["check"] == "show_object_missing" for c in parsed["checks"])
        assert not (isolated_dir / "v1_nos").exists()
        assert not (isolated_dir / "v1_nos_failed").exists()

    # ---- parity ports from tests/test_run.py ----

    def test_syntax_error_caught_by_validation(self, runner, isolated_dir):
        """Syntax errors produce validation_error — no version, no disk."""
        from agentcad.manifest import MANIFEST_FILE
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", "this is not valid python(")
        r = _run(runner, "s.py", "--output", "broken")
        assert r.exit_code == 1
        parsed = json.loads(r.stdout)
        assert parsed["status"] == "validation_error"
        assert any(c["check"] == "syntax_error" for c in parsed["checks"])
        assert not (isolated_dir / "v1_broken_failed").is_dir()
        manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
        assert len(manifest["versions"]) == 0

    def test_validation_error_json_response(self, runner, isolated_dir):
        """validation_error responses include the checks array but no version."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", "this is not valid python(")
        r = _run(runner, "s.py", "--output", "broken")
        parsed = json.loads(r.stdout)
        assert parsed["command"] == "run"
        assert parsed["status"] == "validation_error"
        assert "checks" in parsed
        assert "version" not in parsed

    def test_validation_error_does_not_consume_version(self, runner, isolated_dir):
        """A validation error followed by a clean run lands as v1, not v2."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", "bad(")
        _run(runner, "s.py", "--output", "broken")
        # Next successful run should still be v1.
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "fixed")
        parsed = json.loads(r.stdout)
        assert parsed["version"] == 1
        assert (isolated_dir / "v1_fixed").is_dir()

    def test_failed_does_not_advance_current(self, runner, isolated_dir):
        """A runtime failure (passes validation, fails at exec) leaves
        manifest.current pointing at the previous successful version."""
        from agentcad.manifest import MANIFEST_FILE
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "good")
        # Runtime error: passes validation (has show_object), explodes during exec.
        _write(isolated_dir, "s.py",
               'from build123d import Box\n'
               'raise ValueError("boom")\n'
               'show_object(Box(1, 1, 1))\n')
        _run(runner, "s.py", "--output", "bad")
        manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
        assert manifest["current"] == "good"

    def test_runtime_error_creates_failed_version(self, runner, isolated_dir):
        """Explicit raise during exec creates a v{N}_{label}_failed dir
        with status: failed in meta.json. (Different path than broken
        geometry — that's covered by test_broken_geometry_creates_failed_version.)"""
        _init(runner, isolated_dir)
        script = (
            'from build123d import Box\n'
            'result = Box(10, 10, 10)\n'
            'raise ValueError("something went wrong")\n'
            'show_object(result)\n'
        )
        _write(isolated_dir, "s.py", script)
        r = _run(runner, "s.py", "--output", "broken")
        assert r.exit_code == 1
        failed_dir = isolated_dir / "v1_broken_failed"
        assert failed_dir.is_dir()
        meta = json.loads((failed_dir / "meta.json").read_text())
        assert meta["status"] == "failed"
        assert "something went wrong" in meta["error"]

    def test_failed_no_metrics(self, runner, isolated_dir):
        """Validation failures don't carry a metrics dict."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", "bad(")
        r = _run(runner, "s.py", "--output", "broken")
        parsed = json.loads(r.stdout)
        assert "metrics" not in parsed


# ---------- multi-show_object handling ----------

class TestMultiCompound:
    def test_explicit_compound_no_warning(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", MULTI)
        r = _run(runner, "s.py", "--output", "ok", "--no-preview", "--dry-run")
        parsed = json.loads(r.stdout)
        # One show_object(Compound) should not trigger any warning
        warnings = parsed.get("warnings", [])
        assert not any("show_object" in w for w in warnings)

    def test_multiple_show_objects_emit_parts(self, runner, isolated_dir):
        """Per-part metrics in build123d (issue #69, PR #79). Multiple
        show_object() calls now produce a `parts` array — no compound warning."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py",
               "from build123d import Box, Sphere\n"
               "show_object(Box(1,1,1))\n"
               "show_object(Sphere(2).translate((5,0,0)))\n")
        r = _run(runner, "s.py", "--output", "warn", "--no-preview", "--dry-run")
        parsed = json.loads(r.stdout)
        assert parsed["status"] == "success"
        assert "parts" in parsed
        assert len(parsed["parts"]) == 2
        warnings = parsed.get("warnings", [])
        assert not any("compound" in w for w in warnings)


# ---------- named parts (parity with tests/test_run.py) ----------

class TestNamedParts:
    """Mirror of test_run.py's named-parts coverage on the b3d runtime.

    The cq integration tests (test_run_named_parts_*, test_run_partial_naming_*,
    test_run_duplicate_part_names_*, test_run_single_show_object_has_parts_array,
    test_run_no_preview_skips_per_part_renders, test_run_parts_persisted_in_meta_json,
    test_run_viewer_parts_panel_includes_named_parts) verify run.py's per-part
    handling using cq scripts. These twins exercise the same code path through
    build123d so the behavior stays in lockstep until cadquery is deprecated."""

    def test_named_parts_emits_parts_array(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", NAMED_PARTS)
        r = _run(runner, "s.py", "--output", "three_parts")
        assert r.exit_code == 0, r.output
        parsed = json.loads(r.stdout)

        assert "parts" in parsed
        parts = parsed["parts"]
        assert len(parts) == 3
        assert [p["id"] for p in parts] == ["deck", "pin", "arm"]
        assert [p["id_source"] for p in parts] == ["name", "name", "name"]
        assert [p["name"] for p in parts] == ["deck", "pin", "arm"]
        assert [p["color"] for p in parts] == ["gray", "blue", "red"]
        assert all(p["part_of"] is None for p in parts)

    def test_named_parts_have_metrics(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", NAMED_PARTS)
        r = _run(runner, "s.py", "--output", "parts_metrics")
        parsed = json.loads(r.stdout)

        for p in parsed["parts"]:
            m = p["metrics"]
            for key in (
                "volume", "surface_area", "center_of_mass",
                "bounding_box", "dimensions",
                "face_count", "edge_count", "is_valid",
            ):
                assert key in m, f"missing metric {key!r} on part {p['id']}"
            assert m["volume"] > 0
            assert m["face_count"] > 0

    def test_named_parts_preview_files_exist(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", NAMED_PARTS)
        r = _run(runner, "s.py", "--output", "parts_preview")
        parsed = json.loads(r.stdout)

        for p in parsed["parts"]:
            preview = p.get("preview")
            assert preview, f"part {p['id']} missing preview path"
            preview_path = isolated_dir / preview
            assert preview_path.exists(), f"preview file {preview_path} not written"
            assert preview_path.stat().st_size > 1024
            assert preview_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    def test_parts_persisted_in_meta_json(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", NAMED_PARTS)
        r = _run(runner, "s.py", "--output", "parts_meta")
        parsed = json.loads(r.stdout)
        version_dir = Path(parsed["outputs"]["step"]).parent
        meta = json.loads((isolated_dir / version_dir / "meta.json").read_text())

        assert "parts" in meta
        assert [p["name"] for p in meta["parts"]] == ["deck", "pin", "arm"]

    def test_partial_naming_still_emits_parts(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", PARTIAL_NAMED_PARTS)
        r = _run(runner, "s.py", "--output", "partial")
        parsed = json.loads(r.stdout)

        parts = parsed["parts"]
        assert [p["id"] for p in parts] == ["deck", "part_1", "arm"]
        assert [p["id_source"] for p in parts] == ["name", "generated", "name"]
        assert parts[0].get("name") == "deck"
        assert parts[1].get("name") is None
        assert parts[2].get("name") == "arm"

    def test_partial_naming_preview_falls_back_to_id(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", PARTIAL_NAMED_PARTS)
        r = _run(runner, "s.py", "--output", "partial_preview")
        parsed = json.loads(r.stdout)

        previews = [p["preview"] for p in parsed["parts"]]
        assert previews[0].endswith("/parts/deck.png")
        assert previews[1].endswith("/parts/part_1.png")
        assert previews[2].endswith("/parts/arm.png")

    def test_single_show_object_has_parts_array(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "single")
        parsed = json.loads(r.stdout)

        assert "parts" in parsed
        assert len(parsed["parts"]) == 1
        assert parsed["parts"][0]["id"] == "part_0"
        assert parsed["parts"][0]["id_source"] == "generated"
        assert parsed["parts"][0].get("name") is None

    def test_no_preview_skips_per_part_renders(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", NAMED_PARTS)
        r = _run(runner, "s.py", "--output", "nop", "--no-preview")
        parsed = json.loads(r.stdout)

        assert "parts" in parsed
        for p in parsed["parts"]:
            assert not p.get("preview")

    def test_viewer_glb_uses_requested_part_colors(self, runner, isolated_dir):
        import struct

        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", NAMED_PARTS)
        r = _run(runner, "s.py", "--output", "colored", "--no-preview")
        assert r.exit_code == 0, r.output

        data = (isolated_dir / "v1_colored" / "output.glb").read_bytes()
        json_length = struct.unpack("<I", data[12:16])[0]
        gltf = json.loads(data[20:20 + json_length])
        colors = [
            tuple(m["pbrMetallicRoughness"]["baseColorFactor"])
            for m in gltf["materials"]
        ]
        node_names = [n.get("name") for n in gltf["nodes"]]

        assert set(node_names) >= {"deck", "pin", "arm"}
        assert (0.50, 0.50, 0.50, 1.0) in colors
        assert (0.0, 0.0, 1.0, 1.0) in colors
        assert (1.0, 0.0, 0.0, 1.0) in colors

    def test_duplicate_part_names_are_allowed(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", DUPLICATE_NAMES)
        r = _run(runner, "s.py", "--output", "dup")
        assert r.exit_code == 0, r.output
        parsed = json.loads(r.stdout)

        parts = parsed["parts"]
        assert [p["id"] for p in parts] == ["wheel", "wheel_2"]
        assert [p["id_source"] for p in parts] == ["name", "name"]
        assert [p["name"] for p in parts] == ["wheel", "wheel"]
        # Collision policy: IDs are deduped, so preview filenames do not overwrite.
        previews = [p["preview"] for p in parts]
        assert previews[0].endswith("/parts/wheel.png")
        assert previews[1].endswith("/parts/wheel_2.png")

    def test_explicit_part_id_wins_over_name(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", """\
deck = Box(20, 10, 2)
pin = Cylinder(radius=2, height=5).translate((20, 0, 0))
show_object(deck, id="main-deck", name="Deck")
show_object(pin, options={"id": "pivot_pin", "name": "Pivot Pin", "color": "blue"})
""")
        r = _run(runner, "s.py", "--output", "explicit_parts")
        assert r.exit_code == 0, r.output
        parsed = json.loads(r.stdout)

        parts = parsed["parts"]
        assert [p["id"] for p in parts] == ["main_deck", "pivot_pin"]
        assert [p["id_source"] for p in parts] == ["explicit", "explicit"]
        assert [p["name"] for p in parts] == ["Deck", "Pivot Pin"]

    def test_viewer_parts_panel_includes_named_parts(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", NAMED_PARTS)
        r = _run(runner, "s.py", "--output", "v1")
        assert r.exit_code == 0, r.output

        viewer_html = (isolated_dir / "v1" / "viewer.html").read_text()
        import re
        match = re.search(r"const PARTS = (\[.*?\]);", viewer_html)
        assert match, "PARTS const not found in viewer.html"
        parts = json.loads(match.group(1))
        assert [p.get("name") for p in parts] == ["deck", "pin", "arm"]


# ---------- multi-show (parity with tests/test_run.py) ----------

class TestMultiShow:
    """Mirror of test_run.py's multi-show_object integration coverage on b3d.

    Two separate ``show_object()`` calls (not a single Compound) must
    produce a compound STEP, populate ``parts`` on both run JSON and
    meta.json, expose metrics that span both shapes, and not emit the
    legacy "combined into a single compound" warning. Render + export
    + GLB-material flags must work on the multi-show output."""

    def test_multiple_show_object_produces_compound(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", MULTI_SHOW)
        r = _run(runner, "s.py", "--output", "multi")
        assert r.exit_code == 0, r.output
        step = isolated_dir / "v1_multi" / "output.step"
        assert step.exists()
        assert step.stat().st_size > 0

    def test_multiple_show_object_no_compound_warning(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", MULTI_SHOW)
        r = _run(runner, "s.py", "--output", "multi")
        parsed = json.loads(r.stdout)
        for w in parsed.get("warnings", []):
            assert "combined into a single compound" not in w

    def test_multiple_show_object_meta_has_parts_not_warning(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", MULTI_SHOW)
        _run(runner, "s.py", "--output", "multi")
        meta = json.loads((isolated_dir / "v1_multi" / "meta.json").read_text())
        assert "parts" in meta and len(meta["parts"]) == 2
        for w in meta.get("warnings", []):
            assert "combined into a single compound" not in w

    def test_multiple_show_object_metrics_cover_both(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", MULTI_SHOW)
        r = _run(runner, "s.py", "--output", "multi")
        parsed = json.loads(r.stdout)
        m = parsed["metrics"]
        # Box extends z: -5..5, cyl translated to z=25 extends z: 20..30.
        # Combined z extent must span both shapes.
        z_extent = m["dimensions"]["z"]
        assert z_extent > 25

    def test_single_show_object_no_warning(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "v1")
        parsed = json.loads(r.stdout)
        # Either absent, or present-but-empty — the contract is "no warnings emitted".
        assert not parsed.get("warnings")

    def test_multiple_show_object_with_render(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", MULTI_SHOW)
        r = _run(runner, "s.py", "--output", "multi", "--render", "iso")
        assert r.exit_code == 0
        png = isolated_dir / "v1_multi" / "renders" / "iso.png"
        assert png.exists()
        assert png.read_bytes()[:4] == b"\x89PNG"

    def test_multiple_show_object_with_export(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", MULTI_SHOW)
        r = _run(runner, "s.py", "--output", "multi", "--export", "stl")
        assert r.exit_code == 0
        stl = isolated_dir / "v1_multi" / "output.stl"
        assert stl.exists()
        assert stl.stat().st_size > 0

    def test_export_glb_multi_show_object_has_materials(self, runner, isolated_dir):
        import struct as _struct
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", MULTI_SHOW)
        r = _run(runner, "s.py", "--output", "multi", "--export", "glb")
        assert r.exit_code == 0
        glb_path = isolated_dir / "v1_multi" / "output.glb"
        assert glb_path.exists()
        data = glb_path.read_bytes()
        json_length = _struct.unpack("<I", data[12:16])[0]
        gltf = json.loads(data[20:20 + json_length])
        assert len(gltf["materials"]) >= 2

    def test_multi_show_warnings_key_is_list_type_if_present(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", MULTI_SHOW)
        r = _run(runner, "s.py", "--output", "multi")
        parsed = json.loads(r.stdout)
        if "warnings" in parsed:
            assert isinstance(parsed["warnings"], list)


# ---------- warnings surface (parity with tests/test_run.py) ----------

def _fake_metrics_invalid(real_compute):
    """Wrap compute_metrics to force is_valid=False (mirrors cq fixture)."""
    def wrapper(topo_shape):
        m = real_compute(topo_shape)
        m["is_valid"] = False
        m["validity_errors"] = ["BRepCheck_InvalidToleranceValue"]
        return m
    return wrapper


class TestWarnings:
    """Mirror of test_run.py's M36 validity-warnings + diagnostics coverage
    on the b3d runtime. Locks the contract that:
      - is_valid=False in metrics produces a top-level warnings array
      - validity warnings surface in run JSON, meta.json, and --dry-run
      - negative volume warnings from metrics surface
      - valid shapes emit no warnings
      - warnings.warn() from the script body lands in the run JSON
        (this last one regressed in #142 and was fixed in #146 — pin
        the contract here so future regressions are caught on b3d)."""

    def test_invalid_shape_has_warnings_in_output(self, runner, isolated_dir, monkeypatch):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        from agentcad import metrics
        monkeypatch.setattr(
            "agentcad.metrics.compute_metrics",
            _fake_metrics_invalid(metrics.compute_metrics),
        )
        r = _run(runner, "s.py", "--output", "inv")
        parsed = json.loads(r.stdout)
        assert parsed["status"] == "success"
        assert "warnings" in parsed
        assert any("invalid geometry" in w.lower() for w in parsed["warnings"])

    def test_invalid_shape_warnings_in_meta(self, runner, isolated_dir, monkeypatch):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        from agentcad import metrics
        monkeypatch.setattr(
            "agentcad.metrics.compute_metrics",
            _fake_metrics_invalid(metrics.compute_metrics),
        )
        _run(runner, "s.py", "--output", "inv")
        meta = json.loads((isolated_dir / "v1_inv" / "meta.json").read_text())
        assert "warnings" in meta
        assert any("invalid geometry" in w.lower() for w in meta["warnings"])

    def test_invalid_shape_dry_run_has_warnings(self, runner, isolated_dir, monkeypatch):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        from agentcad import metrics
        monkeypatch.setattr(
            "agentcad.metrics.compute_metrics",
            _fake_metrics_invalid(metrics.compute_metrics),
        )
        r = _run(runner, "s.py", "--output", "inv", "--dry-run")
        parsed = json.loads(r.stdout)
        assert "warnings" in parsed
        assert any("invalid geometry" in w.lower() for w in parsed["warnings"])

    def test_valid_shape_no_warnings(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "v1")
        parsed = json.loads(r.stdout)
        assert "warnings" not in parsed

    def test_negative_volume_warning_surfaces(self, runner, isolated_dir, monkeypatch):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        from agentcad import metrics
        real = metrics.compute_metrics

        def fake(topo_shape):
            m = real(topo_shape)
            m["volume"] = -1000.0
            m["warnings"] = ["Negative volume detected — check winding order."]
            return m
        monkeypatch.setattr("agentcad.metrics.compute_metrics", fake)
        r = _run(runner, "s.py", "--output", "neg")
        parsed = json.loads(r.stdout)
        assert "warnings" in parsed
        assert any("negative volume" in w.lower() for w in parsed["warnings"])

    def test_surfaces_script_warnings_in_json(self, runner, isolated_dir):
        """warnings.warn() from the script body lands in the run JSON.
        Regression for #142 (where the captured-vs-show_object shadowing
        broke this entire path); fixed by #146. Locks it on b3d."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py",
               "import warnings\n"
               "warnings.warn('AGENTCAD_SCRIPT_WARNING')\n"
               "from build123d import Box\n"
               "show_object(Box(10, 10, 10))\n")
        r = _run(runner, "s.py", "--output", "v1")
        assert r.exit_code == 0, r.output
        parsed = json.loads(r.stdout)
        assert "AGENTCAD_SCRIPT_WARNING" in (parsed.get("warnings") or [])


# ---------- auto-diff (parity with tests/test_run.py) ----------

class TestAutoDiff:
    """Mirror of cq's auto-diff coverage on b3d. From v2 onward, run
    produces diff_side.png + diff_overlay.png against the previous
    successful version. Failed versions are skipped when picking the
    diff target."""

    def test_auto_diff_png_when_prior_success(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r1 = _run(runner, "s.py", "--output", "first")
        p1 = json.loads(r1.stdout)
        assert "diff" not in p1  # first version has no prior

        # v2: different geometry — should produce diff
        _write(isolated_dir, "s.py",
               "from build123d import Box\nshow_object(Box(20, 20, 20))\n")
        r2 = _run(runner, "s.py", "--output", "second")
        p2 = json.loads(r2.stdout)
        assert "diff" in p2
        assert p2["diff"]["against"] == "first"
        assert p2["diff"]["side_by_side"] == "v2_second/diff_side.png"
        assert p2["diff"]["overlay"] == "v2_second/diff_overlay.png"
        assert (isolated_dir / "v2_second" / "diff_side.png").exists()
        assert (isolated_dir / "v2_second" / "diff_overlay.png").exists()

    def test_auto_diff_skips_failed_versions(self, runner, isolated_dir):
        """Auto-diff picks the most recent SUCCESSFUL prior, skipping
        failed runs in between."""
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "good")
        # v2: runtime error (passes validation, fails during exec).
        _write(isolated_dir, "s.py",
               'from build123d import Box\n'
               'raise ValueError("boom")\n'
               'show_object(Box(1, 1, 1))\n')
        _run(runner, "s.py", "--output", "bad")
        # v3: success — should diff against v1 (last success), not v2 (failed).
        _write(isolated_dir, "s.py",
               "from build123d import Box\nshow_object(Box(30, 30, 30))\n")
        r3 = _run(runner, "s.py", "--output", "recovered")
        p3 = json.loads(r3.stdout)
        assert "diff" in p3
        assert p3["diff"]["against"] == "good"


# ---------- first-run/second-run viewer + hint (parity) ----------

class TestViewerHints:
    """Mirror of cq's viewer/hint coverage on b3d. The viewer.html
    DEFAULT_MODE flips on the second run; the run JSON's `hint` field
    references the previous version when one exists."""

    def test_first_run_viewer_defaults_to_single_a(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "v1")
        assert r.exit_code == 0, r.output

        viewer_html = (isolated_dir / "v1" / "viewer.html").read_text()
        assert 'DEFAULT_MODE = "single-a"' in viewer_html
        assert 'DEFAULT_MODE = "side-by-side"' not in viewer_html

    def test_second_run_viewer_defaults_to_side_by_side(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "v1")
        r = _run(runner, "s.py", "--output", "v2")
        assert r.exit_code == 0, r.output

        viewer_html = (isolated_dir / "v2" / "viewer.html").read_text()
        assert 'DEFAULT_MODE = "side-by-side"' in viewer_html

    def test_first_run_emits_hint_in_json(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "v1")
        parsed = json.loads(r.stdout)
        assert "hint" in parsed
        assert parsed["viewer"] in parsed["hint"]
        assert "side-by-side" not in parsed["hint"]

    def test_second_run_hint_references_previous(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        _run(runner, "s.py", "--output", "v1")
        r = _run(runner, "s.py", "--output", "v2")
        parsed = json.loads(r.stdout)
        assert "hint" in parsed
        assert "previous" in parsed["hint"]
        assert parsed["viewer"] in parsed["hint"]


# ---------- daemon via field (parity) ----------

class TestDaemonViaField:
    """Mirror of cq's daemon plumbing tests. The run command stamps a
    ``via: daemon`` field on the JSON when the request was routed
    through the background daemon, and omits it on direct execution.
    Runtime-agnostic, but exercising it on b3d locks the contract for
    Phase 6 when more agents will hit b3d via the daemon."""

    def test_via_daemon_field_in_output(self, runner, isolated_dir, monkeypatch):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        # Re-enable daemon routing (undo the autouse _no_daemon fixture).
        monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
        daemon_output = json.dumps({"command": "run", "status": "success", "version": 1})
        # Routing helper extracted to commands/_daemon_routing.py (#177).
        monkeypatch.setattr(
            "agentcad.daemon.send_request",
            lambda *a, **kw: {"type": "result", "exit_code": 0, "output": daemon_output},
        )
        r = _run(runner, "s.py", "--output", "v1")
        assert r.exit_code == 0
        parsed = json.loads(r.stdout)
        assert parsed["via"] == "daemon"

    def test_direct_no_via_field(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "v1")
        assert r.exit_code == 0
        parsed = json.loads(r.stdout)
        assert "via" not in parsed


# ---------- progress heartbeats (parity with tests/test_run.py) ----------

class TestProgressHeartbeats:
    """Mirror of cq's heartbeat coverage on b3d. Issue #164 — stderr
    progress lines per phase so agents can distinguish 'still working'
    from 'wedged' during long preview/GIF phases."""

    def test_emits_progress_heartbeats_on_stderr(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "v1")
        assert r.exit_code == 0, r.output
        stderr = r.stderr
        assert "[agentcad] running script" in stderr
        assert "[agentcad] computing metrics" in stderr
        assert "[agentcad] rendering preview" in stderr
        # Heartbeats stay out of the JSON on stdout (Click 8.3 r.output
        # interleaves stdout + stderr; r.stdout is stdout-only).
        assert "[agentcad]" not in r.stdout
        parsed = json.loads(r.stdout)
        assert parsed["status"] == "success"

    def test_no_preview_skips_preview_heartbeats(self, runner, isolated_dir):
        _init(runner, isolated_dir)
        _write(isolated_dir, "s.py", SIMPLE)
        r = _run(runner, "s.py", "--output", "v1", "--no-preview")
        assert r.exit_code == 0
        stderr = r.stderr
        assert "[agentcad] running script" in stderr
        assert "[agentcad] rendering preview (4-view" not in stderr
