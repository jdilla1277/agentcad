"""Contract tests for the JSON response shape of every agentcad command.

The convention is documented in `agentcad docs schema`:
    outputs — dict of 3D model artifacts (step, script, stl, glb, obj)
    renders — dict of 2D PNG renders (named view or custom angle key)

These tests pin the convention. If a command's response shape drifts
(e.g. a key is renamed, an artifact moves between outputs and renders),
this file breaks and the docs in `commands/docs.py` should be updated
to match.

This is the guardrail that catches the failure mode from PR #78's retro:
an agent assumed `render` returns `outputs` instead of `renders` and
wrote 3 broken tests before grepping the source.
"""

from __future__ import annotations

import json

from agentcad.cli import cli


SIMPLE_SCRIPT = """\
import cadquery as cq
result = cq.Workplane('XY').box(10, 10, 10)
show_object(result)
"""

THREE_D_KEYS = {"step", "script", "stl", "glb", "obj"}
RENDER_KEY_HINTS = {"iso", "front", "back", "left", "right", "top", "bottom"}


def _init(runner):
    return runner.invoke(cli, ["init", "--name", "p"])


def _make_step(runner, isolated_dir):
    """Run a script and return path to its STEP."""
    _init(runner)
    (isolated_dir / "s.py").write_text(SIMPLE_SCRIPT)
    runner.invoke(cli, ["run", "s.py", "--output", "v", "--no-preview"])
    return isolated_dir / "v1_v" / "output.step"


# -------- run --------

def test_run_response_shape(runner, isolated_dir):
    """`run` returns outputs (3D files) and metrics. No renders unless --render."""
    _init(runner)
    (isolated_dir / "s.py").write_text(SIMPLE_SCRIPT)
    r = runner.invoke(cli, ["run", "s.py", "--output", "v", "--no-preview"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    # required fields
    for key in ("command", "status", "runtime", "version", "label", "outputs", "metrics", "timings"):
        assert key in parsed, f"`run` missing required field: {key}"
    assert parsed["command"] == "run"
    assert parsed["status"] == "success"
    # outputs keys must all be 3D-artifact names
    extra = set(parsed["outputs"].keys()) - THREE_D_KEYS
    assert not extra, f"`run.outputs` contains non-3D keys: {extra}"
    # no renders without --render
    assert "renders" not in parsed


def test_run_with_render_returns_renders(runner, isolated_dir):
    """When --render is passed, renders dict appears alongside outputs."""
    _init(runner)
    (isolated_dir / "s.py").write_text(SIMPLE_SCRIPT)
    r = runner.invoke(cli, ["run", "s.py", "--output", "v", "--render", "iso",
                            "--no-preview"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    assert "renders" in parsed, "`run --render` must include `renders` key"
    assert "iso" in parsed["renders"]
    # renders must NOT leak into outputs
    assert "iso" not in parsed["outputs"]


# -------- export --------

def test_export_response_shape(runner, isolated_dir):
    step = _make_step(runner, isolated_dir)
    r = runner.invoke(cli, ["export", str(step), "--format", "stl"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    for key in ("command", "status", "outputs"):
        assert key in parsed, f"`export` missing required field: {key}"
    assert parsed["command"] == "export"
    # outputs keys must be 3D format names — never view names
    for k in parsed["outputs"]:
        assert k in THREE_D_KEYS, (
            f"`export.outputs` key {k!r} is not a 3D format. "
            "Renders must be in `renders`, never `outputs`."
        )
    # export never returns renders
    assert "renders" not in parsed


# -------- render --------

def test_render_response_shape(runner, isolated_dir):
    step = _make_step(runner, isolated_dir)
    r = runner.invoke(cli, ["render", str(step), "--view", "iso"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    for key in ("command", "status", "renders"):
        assert key in parsed, f"`render` missing required field: {key}"
    assert parsed["command"] == "render"
    # render returns ONLY renders, never outputs
    assert "outputs" not in parsed, (
        "`render` must put PNGs under `renders`, not `outputs`. "
        "If this fails, the convention has drifted — update both source "
        "and `agentcad docs schema`."
    )
    # render keys are PNG view names (or --name overrides). Sanity-check
    # that what we asked for came back.
    assert "iso" in parsed["renders"]


# -------- inspect --------

def test_inspect_response_shape(runner, isolated_dir):
    step = _make_step(runner, isolated_dir)
    r = runner.invoke(cli, ["inspect", str(step)])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    for key in ("command", "status", "file", "solid_count", "shell_count",
                "face_count", "edge_count", "is_valid"):
        assert key in parsed, f"`inspect` missing required field: {key}"
    # inspect produces no artifacts
    assert "outputs" not in parsed
    assert "renders" not in parsed


# -------- measure --------

def test_measure_response_shape(runner, isolated_dir):
    step = _make_step(runner, isolated_dir)
    r = runner.invoke(cli, ["measure", str(step)])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    for key in ("command", "status", "file", "metrics", "feature_summary"):
        assert key in parsed, f"`measure` missing required field: {key}"
    assert parsed["command"] == "measure"
    # measure reports data only; it produces no artifacts
    assert "outputs" not in parsed
    assert "renders" not in parsed


# -------- view --------

def test_view_response_shape(runner, isolated_dir):
    step = _make_step(runner, isolated_dir)
    r = runner.invoke(cli, ["view", str(step)])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    for key in ("command", "status", "url", "model"):
        assert key in parsed, f"`view` missing required field: {key}"
    # view is a launcher, not an artifact-producer
    assert "outputs" not in parsed
    assert "renders" not in parsed


# -------- diff --------

def test_diff_response_shape(runner, isolated_dir):
    """Diff between two versions exposes changes split by outputs/renders/metrics."""
    _init(runner)
    (isolated_dir / "s.py").write_text(SIMPLE_SCRIPT)
    runner.invoke(cli, ["run", "s.py", "--output", "a", "--no-preview"])
    # Slightly different script for v2
    (isolated_dir / "s.py").write_text(
        "import cadquery as cq\n"
        "show_object(cq.Workplane('XY').box(20, 10, 10))\n"
    )
    runner.invoke(cli, ["run", "s.py", "--output", "b", "--no-preview"])

    r = runner.invoke(cli, ["diff", "1", "2"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    for key in ("command", "status", "v1", "v2", "changes"):
        assert key in parsed, f"`diff` missing required field: {key}"
    # diff.changes mirrors the artifact convention — outputs and renders
    # are sub-sections, not top-level fields.
    assert "outputs" in parsed["changes"]
    assert "renders" in parsed["changes"]
    # And never at top level
    assert "outputs" not in parsed
    assert "renders" not in parsed


# -------- meta.json on disk --------

def test_meta_json_uses_same_artifact_convention(runner, isolated_dir):
    """meta.json on disk follows the same outputs/renders split as command output."""
    _init(runner)
    (isolated_dir / "s.py").write_text(SIMPLE_SCRIPT)
    runner.invoke(cli, ["run", "s.py", "--output", "v", "--render", "iso",
                        "--export", "stl", "--no-preview"])
    meta = json.loads((isolated_dir / "v1_v" / "meta.json").read_text())
    # 3D files in outputs
    assert "outputs" in meta
    extra = set(meta["outputs"].keys()) - THREE_D_KEYS
    assert not extra, f"meta.outputs has non-3D keys: {extra}"
    # PNGs in renders, not outputs
    assert "renders" in meta
    assert "iso" in meta["renders"]
    assert "iso" not in meta["outputs"]
