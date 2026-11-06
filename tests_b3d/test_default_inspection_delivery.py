"""The real no-options import/report response is bounded and still actionable."""

import asyncio
import base64
import hashlib
import json
from pathlib import Path
import shlex

import pytest
from mcp.types import CallToolResult

from agentcad.cli import cli
from agentcad.geometry_manifest import read_manifest
from agentcad.mcp import server
from tests_b3d.test_inspection_budgets import REAL, _assert_budget
from tests_b3d.test_inspection_report import _distinct_parts
from tests_b3d.test_step_assembly import _nested_step


def invoke(runner, args):
    # Deliberately no full_response_args compatibility helper: these calls
    # exercise the public defaults, including copied recovery actions.
    result = runner.invoke(cli, [*args, '--no-daemon'])
    assert result.stdout, (result.exit_code, result.exception)
    return result, json.loads(result.stdout)


def payload(result):
    if isinstance(result, CallToolResult):
        assert json.loads(result.content[0].text) == result.structuredContent
        return result.structuredContent
    return result


def hashes(paths):
    return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def source(root, kind):
    if kind == 'real':
        return REAL
    if kind == 'many':
        return _distinct_parts(root / 'many.step')
    return _nested_step(root / 'rotated.step', tilted=True)


@pytest.mark.parametrize('kind', ['real', 'assembly', 'many'])
def test_default_import(runner, isolated_dir, monkeypatch, kind):
    monkeypatch.setattr('agentcad.project_viewer.handoff', lambda *a, **kw: None)
    result, data = invoke(runner, ['import', str(source(isolated_dir, kind)), '--init', '--no-view', '--no-diff'])
    assert result.exit_code == 0 and data['core']['status'] == 'success', data
    _assert_budget(data)
    assert data['delivery']['limits']['text_tokens'] == 2000
    assert data['delivery']['target_source'] == 'text_fallback'
    assert data['delivery']['vision_estimation'] == 'unknown'
    assert data['delivery']['estimated_image_tokens'] is None
    assert data['delivery']['estimated_combined_tokens'] is None
    report = data['inspection']
    assert 1 <= len(report['media']) <= 9
    assert report['media'][0]['id'] == 'overall'
    step = isolated_dir / data['outputs']['step']
    saved = read_manifest(step)
    assert report['manifest_id'] == saved['manifest_id']
    assert report['counts'] == saved['counts']
    assert report['quality']['is_valid'] and report['model']['dimensions']
    assert report['recognition']['recognized_feature_count'] is None
    if kind == 'many':
        assert report['counts']['part_definitions'] == 12
    before = hashes([step, step.with_name('geometry_manifest.json'), isolated_dir / 'edit.py'])

    def unexpected(*a, **kw):
        pytest.fail('Full response retrieval repeated CAD work')

    monkeypatch.setattr('agentcad.inspection_report._analysis', unexpected)
    child = isolated_dir / 'child caller'
    child.mkdir()
    monkeypatch.chdir(child)
    retrieved, full = invoke(runner, shlex.split(data['response_snapshot']['retrieve'])[1:])
    assert retrieved.exit_code == 0 and full['historical_response'], full
    assert 'delivery' not in full
    assert full['geometry_manifest']['manifest_id'] == saved['manifest_id']
    assert full['inspection']['manifest_id'] == saved['manifest_id']
    assert set(full['inspection_job']['actions']) == {'status', 'wait', 'resume', 'cancel'}
    assert full['source'] and full['artifacts']
    assert hashes(before) == before


@pytest.mark.parametrize('kind', ['real', 'assembly'])
def test_default_mcp(isolated_dir, kind):
    result = asyncio.run(server.mcp.call_tool('import_cad', {
        'file': str(source(isolated_dir, kind)), 'cwd': str(isolated_dir),
        'initialize': True, 'view': False, 'diff': False,
    }))
    assert isinstance(result, CallToolResult)
    data = payload(result)
    _assert_budget(data)
    assert data['_exit_code'] == 0 and data['core']['status'] == 'success'
    media = data['inspection']['media']
    assert media and media[0]['id'] == 'overall'
    assert len(result.content) == 1 + len(media)
    image_root = Path(data['inspection']['inventory']['image_root'])
    if not image_root.is_absolute():
        image_root = isolated_dir / image_root
    for item, block in zip(media, result.content[1:]):
        png = base64.b64decode(block.data)
        assert png.startswith(b'\x89PNG\r\n\x1a\n')
        assert hashlib.sha256(png).hexdigest() == item['sha256']
        assert png == (image_root / item['png']).read_bytes()
    step = Path(data['outputs']['step'])
    report = payload(server.inspect(str(step), str(isolated_dir), report=True))
    _assert_budget(report)
    assert report['inspection']['manifest_id'] == data['inspection']['manifest_id']
    assert report['delivery']['limits']['text_tokens'] == 2000
    raw = payload(server.inspect(str(step), str(isolated_dir), entity_id=data['inspection']['media'][-1]['id']))
    assert 'delivery' not in raw and raw['entity']['id'] == data['inspection']['media'][-1]['id']


def test_same_key_delivery_modes(runner, isolated_dir, monkeypatch):
    monkeypatch.setattr('agentcad.project_viewer.handoff', lambda *a, **kw: None)
    args = ['import', str(REAL), '--init', '--no-view', '--no-diff', '--idempotency-key', 'default']
    first, data = invoke(runner, args)
    assert first.exit_code == 0
    _assert_budget(data)
    step = isolated_dir / data['outputs']['step']
    request = next((isolated_dir / '.agentcad/import_requests').glob('*/request.json'))
    before = hashes([step, step.with_name('geometry_manifest.json'), request, isolated_dir / 'agentcad.json', isolated_dir / 'edit.py'])
    monkeypatch.setattr('agentcad.commands.import_cmd._import_impl', lambda *a: pytest.fail('Delivery mode repeated import'))
    unbounded, full = invoke(runner, [*args, '--full-response'])
    assert unbounded.exit_code == 0 and full['idempotency']['replayed']
    assert 'delivery' not in full and full['artifacts'] and full['validation']['layers']
    expanded, large = invoke(runner, [*args, '--response-text-budget', '10000'])
    assert expanded.exit_code == 0 and large['idempotency']['replayed']
    _assert_budget(large)
    assert large['inspection_job']['id'] == data['inspection_job']['id']
    assert len(large['inspection']['media']) >= len(data['inspection']['media'])
    assert hashes(before) == before
    assert len(list(isolated_dir.glob('v*_*/output.step'))) == 1


@pytest.mark.parametrize('options', [['--no-report'], ['--no-inspection-image'], ['--max-images', '0']])
def test_default_opt_outs(runner, isolated_dir, monkeypatch, options):
    monkeypatch.setattr('agentcad.project_viewer.handoff', lambda *a, **kw: None)
    result, data = invoke(runner, ['import', str(REAL), '--init', '--no-view', '--no-diff', *options])
    assert result.exit_code == 0
    _assert_budget(data)
    assert data['delivery']['media']['returned'] == 0
    if '--no-report' in options:
        assert 'inspection' not in data
    else:
        assert data['inspection']['media'] == []
    complete, original = invoke(runner, shlex.split(data['response_snapshot']['retrieve'])[1:])
    assert complete.exit_code == 0
    assert Path(original['outputs']['inspection_report']).is_file()
    assert Path(original['outputs']['inspection_bundle']).is_file()


def test_default_quota_recovery(runner, isolated_dir, monkeypatch):
    monkeypatch.setattr('agentcad.project_viewer.handoff', lambda *a, **kw: None)
    config = isolated_dir / 'agentcad.toml'
    config.write_text('[inspection.cache]\nmax_bytes = 1\n')
    result, data = invoke(runner, ['import', str(REAL), '--init', '--no-view', '--no-diff'])
    assert result.exit_code == 0 and data['core']['status'] == 'success'
    _assert_budget(data)
    job = data['inspection_job']
    assert job['status'] == 'resource_limit' and not job['worker_running']
    assert 'max_bytes' in job['message']
    assert set(job['actions']) == {'resume'}
    step = isolated_dir / data['outputs']['step']
    before = hashes([step, step.with_name('geometry_manifest.json'), isolated_dir / 'edit.py', isolated_dir / 'agentcad.json'])
    config.write_text('[inspection.cache]\nmax_bytes = 10485760\n')
    recovered, complete = invoke(runner, shlex.split(job['actions']['resume'])[1:])
    assert recovered.exit_code == 0 and complete['inspection_job']['status'] == 'complete', complete
    _assert_budget(complete)
    assert complete['inspection_job']['id'] == job['id']
    assert hashes(before) == before


def test_default_snapshot_failure(isolated_dir, monkeypatch):
    monkeypatch.setattr('agentcad.inspection_snapshots.SnapshotStore.publish',
                        lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    result = server.import_cad(str(REAL), str(isolated_dir), initialize=True, view=False, diff=False)
    data = payload(result)
    assert data['_exit_code'] == 0 and data['core']['status'] == 'success'
    assert data['delivery']['status'] == 'snapshot_unavailable'
    assert not data['delivery']['budget_satisfied']
    assert len(result.content) == 1
    assert Path(data['outputs']['step']).is_file()
    assert read_manifest(data['outputs']['step'])['manifest_id'] == data['geometry_manifest']['manifest_id']
