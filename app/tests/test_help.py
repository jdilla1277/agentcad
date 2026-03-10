"""Tests for the cadtool --help operational briefing."""
import json

from cadtool.cli import cli


def test_help_mentions_json_output(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    output = result.output
    assert "JSON" in output
    assert '"status"' in output


def test_help_includes_quickstart_workflow(runner):
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "cadtool init" in output
    assert "cadtool run" in output
    assert "show_object" in output


def test_help_documents_preamble(runner):
    """Agent must know cq and helpers are pre-injected."""
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "cq" in output
    assert "pre-injected" in output or "no import" in output


def test_help_lists_helper_functions(runner):
    result = runner.invoke(cli, ["--help"])
    output = result.output
    for helper in ["loft_sections", "tapered_sweep", "naca_wire",
                    "mirror_fuse", "translate", "rotate"]:
        assert helper in output


def test_help_documents_all_commands(runner):
    result = runner.invoke(cli, ["--help"])
    output = result.output
    for cmd in ["init", "run", "render", "export", "inspect",
                "diff", "context", "view", "daemon", "docs"]:
        assert cmd in output


def test_help_shows_run_example(runner):
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "--output" in output
    assert "--render" in output


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
    """Agent should know cadtool docs exists for deep-dive."""
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "cadtool docs" in output


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


def test_help_explains_val_wrapped(runner):
    """Agent needs to know .val().wrapped bridges CadQuery -> TopoDS_Shape."""
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert ".val().wrapped" in output
    assert "TopoDS_Shape" in output


def test_help_documents_dry_run(runner):
    result = runner.invoke(cli, ["--help"])
    assert "--dry-run" in result.output


def test_help_debugging_section(runner):
    """Agent should know the debugging workflow."""
    result = runner.invoke(cli, ["--help"])
    output = result.output
    assert "free_edge_count" in output
    assert "face_orientations" in output
