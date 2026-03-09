import click

from cadtool.commands.context import context
from cadtool.commands.diff import diff
from cadtool.commands.docs import docs
from cadtool.commands.init import init
from cadtool.commands.run import run


@click.group()
def cli():
    """cadtool — a CLI CAD tool for AI agents."""


cli.add_command(context)
cli.add_command(diff)
cli.add_command(docs)
cli.add_command(init)
cli.add_command(run)
