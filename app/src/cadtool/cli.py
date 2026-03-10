import click

from cadtool.commands.context import context
from cadtool.commands.daemon_cmd import daemon
from cadtool.commands.diff import diff
from cadtool.commands.docs import docs
from cadtool.commands.export_cmd import export_cmd
from cadtool.commands.init import init
from cadtool.commands.inspect_cmd import inspect_cmd
from cadtool.commands.render import render
from cadtool.commands.run import run
from cadtool.commands.view import view


@click.group()
def cli():
    """cadtool — a CLI CAD tool for AI agents."""


cli.add_command(context)
cli.add_command(daemon)
cli.add_command(diff)
cli.add_command(docs)
cli.add_command(export_cmd)
cli.add_command(init)
cli.add_command(inspect_cmd)
cli.add_command(render)
cli.add_command(run)
cli.add_command(view)
