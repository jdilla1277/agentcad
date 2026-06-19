import json

from click.testing import CliRunner
from agentcad.cli import cli


def test_docs_returns_full_documentation(runner):
    result = runner.invoke(cli, ["docs"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["status"] == "success"
    assert data["command"] == "docs"
    assert len(data["content"]) > 100


def test_docs_lists_sections(runner):
    result = runner.invoke(cli, ["docs"])
    data = json.loads(result.stdout)
    sections = data["sections"]
    assert "quickstart" in sections
    assert "install" in sections
    assert "commands" in sections
    assert "render" in sections
    assert "export" in sections
    assert "schema" in sections
    assert "helpers" in sections
    assert "metrics" in sections
    assert "measure" in sections
    assert "preamble" in sections
    assert "validation" in sections
    assert "workflow" in sections
    assert "daemon" in sections
    assert "inspect" in sections
    assert "parts" in sections


def test_docs_commands_section(runner):
    result = runner.invoke(cli, ["docs", "commands"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    for cmd in ["init", "run", "render", "measure", "parts", "context", "docs", "diff"]:
        assert cmd in content


def test_docs_render_section(runner):
    result = runner.invoke(cli, ["docs", "render"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "view" in content
    assert "zoom" in content
    assert "focus" in content


def test_docs_schema_section(runner):
    result = runner.invoke(cli, ["docs", "schema"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "success" in content
    assert "failed" in content
    assert "error" in content


def test_docs_workflow_section(runner):
    result = runner.invoke(cli, ["docs", "workflow"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "init" in content
    assert "run" in content


def test_docs_export_section(runner):
    result = runner.invoke(cli, ["docs", "export"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "stl" in content
    assert "glb" in content
    assert "export_glb" in content


def test_docs_helpers_section(runner):
    # Pinned cq — these helpers are listed in the cadquery preamble's
    # helper section; the b3d preamble has a different curated list.
    result = runner.invoke(cli, ["docs", "helpers", "--runtime", "cadquery"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "loft_sections" in content
    assert "tapered_sweep" in content
    assert "naca_wire" in content
    assert "mirror_fuse" in content


def test_docs_install_section(runner):
    result = runner.invoke(cli, ["docs", "install"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "Python 3.10-3.12" in content
    assert "pip install" in content


def test_docs_metrics_section(runner):
    result = runner.invoke(cli, ["docs", "metrics"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "bounding_box" in content
    assert "volume" in content
    assert "surface_area" in content
    assert "face_count" in content


def test_docs_unknown_section_error(runner):
    result = runner.invoke(cli, ["docs", "nonexistent"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "error"
    assert "nonexistent" in data["message"]


def test_docs_runtimes_dispatch_precedence_matches_dispatcher(runner):
    """`docs runtimes` must teach the same precedence order as dispatch.py.

    Precedence per dispatch.py:14 (highest to lowest):
      1. --runtime CLI flag
      2. Script imports
      3. Project manifest
      4. Global default (build123d, post-#163)

    The earlier text claimed the project manifest "wins if set" — that
    contradicts the dispatcher, and three sub-agents in the batch-2 side-by-side
    independently noticed and reported it. This test pins the contract.
    """
    result = runner.invoke(cli, ["docs", "runtimes"])
    assert result.exit_code == 0
    content = json.loads(result.stdout)["content"]

    # Must NOT contain the false claim that the project pin wins unconditionally.
    assert "wins if set" not in content, (
        "project pin does NOT win unconditionally — script imports beat it"
    )
    # Must NOT still advertise cadquery as the global default (post-#163 it's build123d).
    assert "will flip after Phase 6" not in content, (
        "Phase 6 already shipped; the default is build123d"
    )

    # Must teach the correct precedence: --runtime > imports > manifest > default.
    runtime_pos = content.find("--runtime")
    imports_pos = content.find("Script imports")
    manifest_pos = content.find("agentcad.json")
    default_pos = content.find("build123d")
    assert (
        0 <= runtime_pos < imports_pos < manifest_pos
    ), "dispatch precedence should be listed in order"
    assert default_pos >= 0, "must mention build123d as the global default"


def test_docs_works_without_project(runner, isolated_dir):
    # No agentcad.json in isolated_dir
    result = runner.invoke(cli, ["docs"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["status"] == "success"


# --- M17: Quickstart section (Fix 2) ---


def test_docs_quickstart_section(runner):
    # Pinned cq — quickstart shows cq.Workplane idiom on this side.
    result = runner.invoke(cli, ["docs", "quickstart", "--runtime", "cadquery"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "show_object" in content
    assert "cq.Workplane" in content


def test_docs_quickstart_shows_multi_show_object(runner):
    result = runner.invoke(cli, ["docs", "quickstart"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "compound" in content.lower()


# --- M17: Units note in metrics (Fix 3) ---


def test_docs_metrics_mentions_units(runner):
    result = runner.invoke(cli, ["docs", "metrics"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "unit-agnostic" in content


# --- M17: tapered_sweep limitation (Fix 4) ---


def test_docs_helpers_tapered_sweep_limitation(runner):
    result = runner.invoke(cli, ["docs", "helpers", "--runtime", "cadquery"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "smooth spines" in content


# --- M17: Helper type conversion (Fix 5) ---


def test_docs_helpers_conversion_patterns(runner):
    result = runner.invoke(cli, ["docs", "helpers", "--runtime", "cadquery"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "cq.Shape.cast" in content


# --- M18: Preview in commands docs ---


def test_docs_commands_mentions_preview(runner):
    result = runner.invoke(cli, ["docs", "commands"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    # Preview is on by default; docs should advertise the --no-preview escape hatch
    assert "--no-preview" in content
    assert "preview" in content


# --- M19: Colored GLB in export docs ---


def test_docs_export_mentions_colored_glb(runner):
    result = runner.invoke(cli, ["docs", "export"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"].lower()
    assert "color" in content or "per-solid" in content


# --- M20: Patterns section ---


def test_docs_patterns_section(runner):
    result = runner.invoke(cli, ["docs", "patterns"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "build" in data["content"].lower() and "origin" in data["content"].lower()


def test_docs_patterns_mentions_compound_vs_union(runner):
    # cq-specific: makeCompound is a cq.Workplane API, b3d uses Compound.
    result = runner.invoke(cli, ["docs", "patterns", "--runtime", "cadquery"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "makeCompound" in content
    assert "union" in content


def test_docs_patterns_mentions_revolve(runner):
    # cq docs cover .revolve() on a Workplane; b3d patterns docs mention
    # revolve too but in a different context — pin cq to keep this test's
    # original intent (cq patterns content).
    result = runner.invoke(cli, ["docs", "patterns", "--runtime", "cadquery"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "revolve" in data["content"].lower()


# --- M21: Parametric docs ---


def test_docs_parametric_section(runner):
    result = runner.invoke(cli, ["docs", "parametric"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "--params" in content
    assert "key=value" in content


# --- M22: Rotate direction convention ---


def test_docs_helpers_rotate_direction_convention(runner):
    result = runner.invoke(cli, ["docs", "helpers", "--runtime", "cadquery"])
    data = json.loads(result.stdout)
    content = data["content"]
    assert "right-hand rule" in content.lower() or "counterclockwise" in content.lower()


# --- M22: Angled positioning example ---


def test_docs_patterns_angled_positioning_example(runner):
    result = runner.invoke(cli, ["docs", "patterns"])
    data = json.loads(result.stdout)
    content = data["content"]
    assert "rotate(" in content and "translate(" in content
    assert "arm" in content.lower() or "angle" in content.lower()


# --- Daemon docs (auto-managed: minimal surface area) ---


def test_docs_daemon_section(runner):
    """Daemon docs reflect the auto-managed reality: agents don't start the
    daemon, but `stop` and `status` are kept as diagnostic commands. `start`
    and `restart` were removed from the docs because auto-spawn replaces them."""
    result = runner.invoke(cli, ["docs", "daemon"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    # The kept diagnostic commands.
    assert "daemon stop" in content
    assert "daemon status" in content
    # Auto-managed framing: the daemon spawns itself.
    assert "auto" in content.lower() or "automatic" in content.lower()


def test_docs_commands_does_not_list_daemon(runner):
    """`docs commands` is the agent-facing command listing. The daemon is
    auto-managed and shouldn't appear there — agents who don't know about
    the daemon should never need to learn."""
    result = runner.invoke(cli, ["docs", "commands"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "daemon" not in data["content"].lower()


# --- M24: Inspect docs ---


def test_docs_inspect_section(runner):
    result = runner.invoke(cli, ["docs", "inspect"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "solid_count" in content
    assert "shell" in content.lower()
    assert "free_edge" in content


def test_docs_commands_mentions_inspect(runner):
    result = runner.invoke(cli, ["docs", "commands"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "inspect" in data["content"]


def test_docs_measure_section(runner):
    result = runner.invoke(cli, ["docs", "measure"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "agentcad measure" in content
    assert "diameter" in content
    assert "cylindrical_features" in content
    assert "not a manufacturing tolerance" in content
    assert "--features" in content


# --- M26: twistExtrude performance warning ---


def test_docs_patterns_mentions_twist_extrude(runner):
    # cq-specific: twistExtrude is a cq.Workplane method.
    result = runner.invoke(cli, ["docs", "patterns", "--runtime", "cadquery"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "twistExtrude" in content
    assert "polyline" in content


# --- M33: Parts docs ---


def test_docs_parts_section(runner):
    # Parts docs are runtime-aware (cq vs b3d show different examples);
    # this asserts the cq-tagged content. b3d parts docs have a parallel
    # test in tests_b3d/.
    result = runner.invoke(cli, ["docs", "parts", "--runtime", "cadquery"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "show_object" in content
    assert "name=" in content
    assert "color" in content.lower()
    assert "metrics" in content.lower()
    assert "diff" in content.lower()


# --- M35: Wire helpers + elliptical_sweep docs ---


def test_docs_helpers_mentions_wire_helpers(runner):
    result = runner.invoke(cli, ["docs", "helpers", "--runtime", "cadquery"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "ellipse_wire" in content
    assert "spline_wire" in content
    assert "polygon_wire" in content
    assert "rounded_rect_wire" in content
    assert "elliptical_sweep" in content


def test_docs_preamble_mentions_wire_helpers(runner):
    result = runner.invoke(cli, ["docs", "preamble"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    content = data["content"]
    assert "ellipse_wire" in content
    assert "elliptical_sweep" in content


# --- M38: Complex profile patterns ---


def test_docs_patterns_subtractive_construction(runner):
    """Patterns should document cut-from-blank strategy."""
    result = runner.invoke(cli, ["docs", "patterns", "--runtime", "cadquery"])
    assert result.exit_code == 0
    content = json.loads(result.stdout)["content"].lower()
    assert "subtractive" in content or "cut from" in content
    assert "boolean" in content or "brepalgoapiapi_cut" in content or "cut" in content


def test_docs_patterns_wire_winding(runner):
    """Patterns should document wire winding direction and normals."""
    result = runner.invoke(cli, ["docs", "patterns", "--runtime", "cadquery"])
    assert result.exit_code == 0
    content = json.loads(result.stdout)["content"].lower()
    assert "winding" in content
    assert "ccw" in content or "counter-clockwise" in content
    assert "normal" in content


def test_docs_patterns_mixed_edge_wire(runner):
    """Patterns should document building wires from mixed edge types."""
    result = runner.invoke(cli, ["docs", "patterns", "--runtime", "cadquery"])
    assert result.exit_code == 0
    content = json.loads(result.stdout)["content"]
    assert "BRepBuilderAPI_MakeWire" in content
    assert "BRepBuilderAPI_MakeEdge" in content
