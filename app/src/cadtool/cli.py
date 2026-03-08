import click

from cadtool.commands.init import init
from cadtool.commands.run import run


@click.group()
def cli():
    """cadtool — a CLI CAD tool for AI agents."""


cli.add_command(init)
cli.add_command(run)
