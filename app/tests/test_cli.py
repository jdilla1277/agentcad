from click.testing import CliRunner
from cadtool.cli import cli


def test_cli_group_exists(runner):
    result = runner.invoke(cli, ["--help"])
    assert "cadtool" in result.output
    assert result.exit_code == 0


def test_help_flag(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "cadtool" in result.output


def test_init_subcommand_registered(runner):
    result = runner.invoke(cli, ["init", "--help"])
    assert result.exit_code == 0


def test_unknown_subcommand_fails(runner):
    result = runner.invoke(cli, ["nonexistent"])
    assert result.exit_code != 0


def test_run_subcommand_registered(runner):
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
