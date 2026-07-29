"""Tests for the agentcad --help how-to guide and command reference."""
import json

from agentcad.cli import cli


def test_help_mentions_json_output(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    output = result.output
    assert "JSON" in output
    assert '"status"' in output


def test_help_includes_quickstart_workflow(runner):
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "agentcad init" in output
    assert "agentcad run" in output
    assert "show_object" in output


def test_help_points_at_docs_for_preamble(runner):
    """--help used to inline the cadquery-specific preamble; it now delegates
    to `agentcad docs preamble` so the content can be runtime-aware."""
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "agentcad docs preamble" in output
    assert "pre-injected" in output


def test_default_help_teaches_one_build123d_authoring_api(runner, isolated_dir):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    output = result.output

    assert "BUILD123D AUTHORING" in output
    assert "box = Box(10, 20, 5)" in output
    assert "CadQuery compatibility" in output
    assert "CHOOSING A RUNTIME" not in output
    assert "cq.Workplane" not in output
    assert "build123d or CadQuery" not in output
    assert "CadQuery or build123d" not in output
    assert "cadquery|build123d" not in output
    assert "cadquery or build123d" not in output
    assert "\x08" not in output
    assert "    $ agentcad docs preamble" in output
    assert "    $ agentcad docs quickstart" in output


def test_cadquery_project_help_teaches_only_compatibility_api(
    runner, isolated_dir
):
    init_result = runner.invoke(
        cli,
        ["init", "--name", "legacy", "--runtime", "cadquery"],
    )
    assert init_result.exit_code == 0

    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    output = result.output

    assert "CADQUERY COMPATIBILITY AUTHORING" in output
    assert "box = cq.Workplane" in output
    assert "box = Box(10, 20, 5)" not in output
    assert "run         Execute a build123d script" not in output
    assert "run         Execute the project's CAD script" in output
    assert "CHOOSING A RUNTIME" not in output
    assert "\x08" not in output


def test_cadquery_preamble_docs_list_helpers(runner, isolated_dir):
    """The CadQuery preamble docs must list the helpers
    that --help used to enumerate directly."""
    result = runner.invoke(cli, ["docs", "preamble", "--runtime", "cadquery"])
    content = json.loads(result.stdout)["content"]
    for helper in ["loft_sections", "tapered_sweep", "naca_wire",
                   "mirror_fuse", "translate", "rotate"]:
        assert helper in content


def test_help_documents_all_commands(runner):
    """The top-level --help advertises every agent-facing command. The
    `daemon` group is auto-managed and intentionally hidden — see
    `test_help_does_not_advertise_daemon` below."""
    result = runner.invoke(cli, ["--help"])
    output = result.output
    guide = output.split("QUICK START", 1)[1]
    for cmd in ["init", "run", "import", "render", "export", "measure",
                "check-spec", "inspect", "parts", "diff", "context", "view",
                "docs", "skill", "feedback", "subscribe"]:
        assert f"agentcad {cmd}" in guide
    assert "parts view" in output
    assert "--spec spec.json" in output


def test_help_does_not_advertise_daemon(runner):
    """The daemon is automatic — fresh agents shouldn't be told about it
    in --help. Power users can still invoke `agentcad daemon status` etc.,
    but the group is `hidden=True` on Click so it doesn't clutter the
    default help surface."""
    result = runner.invoke(cli, ["--help"])
    assert "daemon" not in result.output.lower()


def test_help_shows_run_example(runner):
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "--output" in output
    assert "--render" in output


def test_help_mentions_part_review_views(runner):
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "agentcad parts view" in output
    assert "--isolate ID" in output
    assert "--isolate-group GROUP" in output
    assert "--label TEXT" in output
    assert "--note TEXT" in output
    assert "--ghost-rest" in output
    assert "temporary part review handoff viewer" in output
    assert "Browser changes are not saved" in output


def test_help_preserves_docs_page_layout(runner):
    """Examples stay copyable instead of being reflowed into one paragraph."""
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "\x08" not in output
    assert "    $ agentcad docs quickstart" in output
    assert "    $ agentcad docs preamble" in output
    assert "\n    {\"agentcad\": {\"command\": \"python\"" in output


def test_help_documents_feature_flags_missing_from_old_guide(runner):
    result = runner.invoke(cli, ["--help"])
    output = result.output
    for flag in [
        "--force", "--label LABEL", "--init", "--diameter N",
        "--tolerance N", "--axis", "--visual", "--overlay", "--measure",
        "--spec spec.json", "--focus-group GROUP", "--no-open",
        "--max-entries N", "--local-only",
    ]:
        assert flag in output
    assert "Passing a STEP/STP/BREP path dispatches" in output
    assert "stl, glb, obj" in output


def test_help_documents_status_values(runner):
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "success" in output
    assert "failed" in output
    assert "error" in output
    assert "validation_error" in output


def test_help_mentions_metrics(runner):
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "metrics" in output
    assert "volume" in output


def test_help_mentions_spec_and_measurement_checks(runner):
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "SPEC AND MEASUREMENT CHECKS" in output
    assert "explicit holes, bores, diameters, counts" in output
    assert "agentcad measure" in output
    assert "agentcad check-spec" in output
    assert "Revise before marking the model done" in output


def test_help_mentions_parametric(runner):
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "--params" in output


def test_help_mentions_inspect_for_debugging(runner):
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "inspect" in output
    assert "topology" in output or "shell" in output


def test_help_mentions_render_view_specs(runner):
    """Agent needs to know about named views, 'all', and custom angles."""
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "iso" in output
    assert "front" in output


def test_help_mentions_patterns(runner):
    """Agent should know key CadQuery patterns."""
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "show_object" in output


def test_help_mentions_docs_command(runner):
    """Agent should know agentcad docs exists for deep-dive."""
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "agentcad docs" in output


def test_help_shows_example_json_output(runner):
    """Agent needs to see what the actual JSON response looks like."""
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert '"command": "run"' in output
    assert '"status": "success"' in output
    assert '"version": 1' in output
    assert '"outputs"' in output


def test_help_documents_version_directory_layout(runner):
    """Agent needs to know where files land on disk."""
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "output.step" in output
    assert "meta.json" in output
    assert "script.py" in output
    assert "preview.png" in output


def test_cadquery_docs_explain_val_wrapped(runner, isolated_dir):
    """.val().wrapped bridges CadQuery -> TopoDS_Shape. Used to live in --help
    directly; now lives in the cadquery docs sections so the build123d
    preamble doesn't get polluted with CadQuery-only idioms."""
    # The bridge idiom is documented in either preamble or helpers section.
    full = runner.invoke(cli, ["docs", "--runtime", "cadquery"])
    content = json.loads(full.stdout)["content"]
    assert ".val().wrapped" in content or "val().wrapped" in content
    assert "TopoDS_Shape" in content


def test_help_documents_dry_run(runner):
    result = runner.invoke(cli, ["--help"])
    assert "--dry-run" in result.output


def test_help_debugging_section(runner):
    """Agent should know the debugging workflow."""
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "free_edge_count" in output
    assert "face_orientations" in output


def test_help_no_preview_scoped_to_composite_only(runner):
    """--no-preview now only skips the 4-view composite (and per-part
    previews); viewer.html, GLB, and diff PNGs always generate so the
    viewer experience stays intact. The --help text must reflect that
    scope — the old 'skip to keep runs sub-second' framing was an active
    invitation to drop the whole pipeline, which is what produced the
    'viewer loads with only one version' problem."""
    result = runner.invoke(cli, ["--help"])
    output = result.output
    # New framing: scope --no-preview to the composite, name what stays.
    assert "4-view composite" in output
    assert "no-preview" in output
    assert "viewer.html" in output
    # No more references to the removed auto-GIF or the old "sub-second"
    # invitation — both pushed agents toward --no-preview.
    assert "60-frame turntable GIF" not in output
    assert "sub-second" not in output
    # The pre-#165 cosmetic framing also stays gone.
    assert "256x256" not in output
    assert "Quick 256" not in output


def test_run_subcommand_help_no_preview_scoped_to_preview_pngs(runner):
    """Subcommand help says both kinds of preview PNG are skipped while
    viewer, GLB, and diff artifacts remain enabled."""
    result = runner.invoke(cli, ["run", "--help"])
    output = result.output
    assert "no-preview" in output
    assert "4-view composite" in output
    assert "per-part previews" in output
    assert "viewer.html" in output
    # Old "iterating to keep runs sub-second" framing is gone.
    assert "sub-second" not in output
    assert "256x256" not in output


def test_help_presents_automatic_previous_current_review(runner):
    full_help = runner.invoke(cli, ["--help"]).output
    run_help = runner.invoke(cli, ["run", "--help"]).output

    assert "A=previous, B=current" in full_help
    assert "--view / --no-view" in full_help
    assert "--view / --no-view" in run_help
    assert "previous/current A/B comparison" in run_help


def test_help_example_runtime_defaults_to_dispatch_default(runner, isolated_dir):
    """Outside any project, EXAMPLE SESSION shows whatever the global
    DEFAULT_RUNTIME currently is. Tracks the constant rather than
    hardcoding so Phase-6-style flips carry the test along
    automatically. Closes #96 (runtime-aware EXAMPLE SESSION)."""
    from agentcad.runners import dispatch

    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    expected_default = dispatch.DEFAULT_RUNTIME
    expected_other = "build123d" if expected_default == "cadquery" else "cadquery"
    assert f'"runtime": "{expected_default}"' in result.output
    assert f'"runtime": "{expected_other}"' not in result.output


def test_help_example_runtime_follows_build123d_project(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "b3d_test", "--runtime", "build123d"])
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert '"runtime": "build123d"' in result.output
    assert '"runtime": "cadquery"' not in result.output


def test_help_example_runtime_follows_cadquery_project(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "cq_test", "--runtime", "cadquery"])
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert '"runtime": "cadquery"' in result.output
    assert '"runtime": "build123d"' not in result.output
    assert "--runtime cadquery" in result.output


def test_help_example_uses_default_build123d_init(runner, isolated_dir):
    """The default example should demonstrate that build123d needs no flag."""
    runner.invoke(cli, ["init", "--name", "b3d_consistency", "--runtime", "build123d"])
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    output = result.output
    assert "EXAMPLE SESSION" in output
    example_block = output.split("EXAMPLE SESSION", 1)[1].split(
        "VERSION OUTPUTS", 1
    )[0]
    assert "agentcad init" in example_block
    assert "--runtime" not in example_block
    assert '"runtime": "build123d"' in example_block
