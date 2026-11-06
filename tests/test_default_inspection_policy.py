"""CLI policy defaults and the explicit full-response compatibility option."""

import json

import click
from click.testing import CliRunner
import pytest

from agentcad.inspection_delivery import delivery_options, forward_options


def command(name):
    @click.command(name=name)
    @click.argument('file')
    @click.option('--report', 'with_report', is_flag=True)
    @click.option('--response-snapshot')
    @delivery_options
    def wrapped(file, with_report, response_snapshot):
        ctx = click.get_current_context()
        click.echo(json.dumps({'enabled': 'inspection_delivery_policy' in ctx.meta,
                               'policy': ctx.meta.get('inspection_delivery_policy'),
                               'argument': ctx.meta.get('inspection_delivery_argument'),
                               'forwarded': forward_options([])}))
    return wrapped


@pytest.mark.parametrize('name,options,enabled', [
    ('import', [], True), ('import', ['--full-response'], False),
    ('inspect', ['--report'], True), ('inspect', [], False),
    ('inspect', ['--report', '--full-response'], False),
    ('inspect', ['--full-response'], False),
    ('inspect', ['--report', '--response-snapshot', 'delivery_saved'], False),
    ('inspect', ['--report', '--response-snapshot', 'delivery_saved', '--response-text-budget', '4000'], True),
])
def test_policy_selection(name, options, enabled):
    result = CliRunner().invoke(command(name), ['supplier.step', *options])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data['enabled'] is enabled
    if enabled:
        assert data['argument'] == 'supplier.step'
    assert ('--full-response' in data['forwarded']) == ('--full-response' in options)
    if '--response-text-budget' in options:
        assert data['policy']['response_text_budget'] == 4000
        assert data['forwarded'] == ['--response-text-budget', '4000']
    elif enabled:
        assert data['policy'] == {}


@pytest.mark.parametrize('name,options', [
    ('import', ['--full-response', '--response-text-budget', '4000']),
    ('inspect', ['--report', '--full-response', '--context-window', '128000']),
    ('inspect', ['--response-budget', '1000']),
])
def test_incompatible_modes_are_explicit(name, options):
    result = CliRunner().invoke(command(name), ['supplier.step', *options])
    assert result.exit_code == 2
    assert '--full-response' in result.output or '--report' in result.output


def test_default_does_not_replace_the_commands_file_diagnostics():
    result = CliRunner().invoke(command('import'), ['bad\0.step'])
    assert result.exit_code == 0
    assert json.loads(result.output)['argument'] == 'bad\0.step'
