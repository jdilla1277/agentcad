"""Real geometry survives derived-cache expiry, quota eviction and job resumption."""

import asyncio
import hashlib
import json
from pathlib import Path
import shlex

import pytest

from agentcad.inspection_cache import CacheLease, CacheResourceLimit
from agentcad.inspection_jobs import InspectionJob
from agentcad.mcp import server
from agentcad.project import ProjectLayout
from tests_b3d.test_inspection_budgets import REAL, _invoke, _payload, _assert_budget
from tests_b3d.test_inspection_report import _baseline


def _config(root, **values):
    (root / "agentcad.toml").write_text('[inspection.cache]\n' + '\n'.join(f'{key} = {value}' for key, value in values.items()))


def _hashes(paths):
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


@pytest.mark.parametrize('real', [False, True])
def test_cache_expiry_preserves_ids_and_expired_snapshot_images_are_not_replaced(runner, isolated_dir, monkeypatch, real):
    step, manifest = _baseline(isolated_dir, REAL if real else None)
    _config(isolated_dir, max_age_seconds=1)
    monkeypatch.setattr('agentcad.inspection_cache._now', lambda: 1000)
    first, report = _invoke(runner, ['inspect', str(step), '--report', '--max-images', '1', '--response-text-budget', '6000'])
    assert first.exit_code == 0, report
    _assert_budget(report)
    assert report['inspection']['retention']['state'] == 'retained'
    assert report['inspection']['retention']['may_evict_earlier']
    before = _hashes([step, step.with_name('geometry_manifest.json'), Path(report['response_snapshot']['path'])])
    monkeypatch.setattr('agentcad.inspection_cache._now', lambda: 1001)
    recovered = asyncio.run(server.mcp.call_tool('inspect', {
        'file': str(step), 'cwd': str(isolated_dir), 'report': True,
        'response_snapshot': report['response_snapshot']['id'], 'response_text_budget': 6000,
}))
    historical = _payload(recovered)
    assert historical['historical_response'] and historical['_exit_code'] == 1
    assert historical['status'] == 'error' and 'expired' in historical['image']['message']
    assert all(block.type == 'text' for block in recovered.content)
    fresh, current = _invoke(runner, ['inspect', str(step), '--report', '--max-images', '1'])
    assert fresh.exit_code == 0 and current['manifest_id'] == manifest['manifest_id']
    assert current['retention']['expires_at'] > report['inspection']['retention']['expires_at']
    assert _hashes(before) == before


@pytest.mark.parametrize('text_budget', [6000, 3000, 2000])
def test_import_resource_limit_resumes_same_job_without_another_version(runner, isolated_dir, monkeypatch, text_budget):
    monkeypatch.setattr('agentcad.project_viewer.handoff', lambda *a, **kw: None)
    _config(isolated_dir, max_bytes=1)
    imported, response = _invoke(runner, ['import', str(REAL), '--init', '--no-view', '--no-diff',
        '--idempotency-key', 'cache-quota', '--max-images', '1', '--response-text-budget', str(text_budget)])
    assert imported.exit_code == 0 and response['core']['status'] == 'success'
    _assert_budget(response)
    job = response['inspection_job']
    assert job['status'] == 'resource_limit' and not job['worker_running']
    assert '--job-action resume' in job['actions']['resume']
    if text_budget == 6000:
        assert 'max_bytes' in job['message']
    else:
        assert set(job['actions']) == {'resume'}
    step = Path(response['outputs']['step'])
    before = _hashes([step, step.with_name('geometry_manifest.json'), isolated_dir / 'agentcad.json',
                      isolated_dir / '.agentcad/changes/working.json', isolated_dir / 'edit.py',
                      step.parent / 'inspection/jobs' / job['id'] / 'request.json'])
    _config(isolated_dir, max_bytes=10485760)
    invoked, completed = _invoke(runner, shlex.split(job['actions']['resume'])[1:])
    assert invoked.exit_code == 0 and completed['inspection_job']['status'] == 'complete', completed
    assert completed['inspection_job']['id'] == job['id']
    result = asyncio.run(server.mcp.call_tool('inspect', {
        'file': str(step), 'cwd': str(isolated_dir), 'report': True,
        'job_id': job['id'], 'job_action': 'wait', 'response_text_budget': 6000,
}))
    resumed = _payload(result)
    assert resumed['_exit_code'] == 0 and resumed['inspection_job']['status'] == 'complete', resumed
    assert resumed['inspection_job']['id'] == job['id']
    assert any(block.type == 'image' for block in result.content)
    assert resumed['inspection']['retention']['state'] == 'retained'
    assert resumed['inspection']['retention']['expires_at']
    _assert_budget(resumed)
    assert _hashes(before) == before
    assert len(list(isolated_dir.glob('v*_*/output.step'))) == 1


def test_partial_job_keeps_checkpoint_bytes_and_reuses_them_after_quota_failure(runner, isolated_dir, monkeypatch):
    step, _ = _baseline(isolated_dir, REAL)
    original = CacheLease.publish_image

    def constrained(cache, temporary, png, metadata, body):
        if not png.name.startswith('overall_'):
            cache.max_bytes = sum(cache._bytes(e) for e in cache.state['groups'].values()) + cache.job_bytes
        return original(cache, temporary, png, metadata, body)

    with monkeypatch.context() as phase:
        phase.setattr(CacheLease, 'publish_image', constrained)
        prepared = InspectionJob.prepare(step, isolated_dir, ProjectLayout(isolated_dir, isolated_dir), max_images=2)
        invoked, response = _invoke(runner, ['inspect', str(step), '--report', '--job', prepared.job_id, '--job-action', 'resume'])
    assert invoked.exit_code == 1 and response['inspection_job']['status'] == 'resource_limit', response
    job = response['inspection_job']
    state = json.loads(Path(job['checkpoints']).read_text())
    completed = [Path(row['path']) for stage in state['stages'].values() if stage['status'] == 'complete' for row in stage['artifacts']]
    assert len(completed) == 3  # analysis, overall PNG and sidecar
    before = _hashes([step, step.with_name('geometry_manifest.json'), Path(job['request']), *completed])
    from agentcad.render import render_geometry_sheet

    def guarded_render(*args, **kwargs):
        assert not kwargs['title'].startswith('MODEL'), 'Completed overall image was rendered again'
        return render_geometry_sheet(*args, **kwargs)

    monkeypatch.setattr('agentcad.render.render_geometry_sheet', guarded_render)
    resumed, complete = _invoke(runner, shlex.split(job['actions']['resume'])[1:])
    assert resumed.exit_code == 0 and complete['inspection_job']['status'] == 'complete', complete
    assert complete['inspection_job']['id'] == job['id']
    assert _hashes(before) == before


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_foreign_report_cannot_rebuild_an_owners_protected_analysis(runner, isolated_dir, monkeypatch, damage):
    step, saved = _baseline(isolated_dir, REAL)

    def stop_before_image(*args, **kwargs):
        raise CacheResourceLimit('Stop after the completed analysis checkpoint')

    with monkeypatch.context() as phase:
        phase.setattr(CacheLease, 'publish_image', stop_before_image)
        job = InspectionJob.prepare(step, isolated_dir, ProjectLayout(isolated_dir, isolated_dir), max_images=1)
        invoked, response = _invoke(runner, ['inspect', str(step), '--report', '--job', job.job_id, '--job-action', 'resume'])
    assert invoked.exit_code == 1 and response['inspection_job']['status'] == 'resource_limit'
    state = json.loads(Path(response['inspection_job']['checkpoints']).read_bytes())
    analysis = [Path(row['path']) for row in state['stages']['analysis']['artifacts']]
    assert len(analysis) == 1
    path = analysis[0]
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'corrupt analysis checkpoint')
    before = _hashes([step, step.with_name('geometry_manifest.json'), Path(response['inspection_job']['checkpoints'])])
    expected = path.read_bytes() if path.exists() else None
    own, error = _invoke(runner, ['inspect', str(step), '--report', '--no-images'])
    assert own.exit_code == 1 and error['reason'] == 'inspection_cache_unavailable'
    foreign = isolated_dir.parent / (isolated_dir.name + '_foreign')
    foreign.mkdir()
    monkeypatch.chdir(foreign)
    outside, error = _invoke(runner, ['inspect', str(step), '--report', '--no-images'])
    assert outside.exit_code == 1 and error['reason'] == 'inspection_cache_unavailable', error
    assert '--build-dir' in error['message']
    assert (path.read_bytes() if path.exists() else None) == expected
    separate, fresh = _invoke(runner, ['inspect', str(step), '--report', '--no-images', '--build-dir', str(foreign / 'build')])
    assert separate.exit_code == 0 and fresh['manifest_id'] == saved['manifest_id'], fresh
    assert (path.read_bytes() if path.exists() else None) == expected
    assert _hashes(before) == before


def test_expired_complete_job_fails_with_fresh_report_action(runner, isolated_dir, monkeypatch):
    step, saved = _baseline(isolated_dir)
    _config(isolated_dir, max_age_seconds=1)
    monkeypatch.setattr('agentcad.inspection_cache._now', lambda: 1000)
    prepared = InspectionJob.prepare(step, isolated_dir, ProjectLayout(isolated_dir, isolated_dir), images=False)
    result, response = _invoke(runner, ['inspect', str(step), '--report', '--job', prepared.job_id, '--job-action', 'resume'])
    assert result.exit_code == 0 and response['inspection_job']['status'] == 'complete'
    monkeypatch.setattr('agentcad.inspection_cache._now', lambda: 1001)
    stale, error = _invoke(runner, shlex.split(response['inspection_job']['actions']['wait'])[1:])
    assert stale.exit_code == 1 and error['reason'] == 'inspection_cache_unavailable', error
    assert 'expired' in error['message']
    fresh, current = _invoke(runner, shlex.split(error['next_actions'][0])[1:] + ['--no-images'])
    assert fresh.exit_code == 0 and current['manifest_id'] == saved['manifest_id']


def test_external_job_canceled_after_catalogue_resumes_with_unchanged_checkpoints(runner, isolated_dir, monkeypatch):
    caller, source = isolated_dir / 'caller', isolated_dir / 'supplier'
    caller.mkdir()
    source.mkdir()
    step, saved = _baseline(source, REAL)
    monkeypatch.chdir(caller)
    job = InspectionJob.prepare(step, source, ProjectLayout(caller, caller), max_images=1)
    checkpoint = job._checkpoint

    def cancel_after_catalogue(name, status, paths):
        checkpoint(name, status, paths)
        if name == 'catalogue' and status == 'complete':
            job.cancel()

    with monkeypatch.context() as phase:
        phase.setattr(job, '_checkpoint', cancel_after_catalogue)
        job.start()
    canceled = job.status()
    assert canceled['status'] == 'canceled' and not canceled['worker_running']
    state = json.loads(Path(canceled['checkpoints']).read_bytes())
    assert state['stages']['catalogue']['status'] == 'complete' and 'result' not in state
    paths = [step, step.with_name('geometry_manifest.json'), Path(canceled['request'])]
    paths += [Path(row['path']) for stage in state['stages'].values() for row in stage.get('artifacts', [])]
    before = _hashes(paths)
    mtimes = {p: p.stat().st_mtime_ns for p in paths}

    def unexpected(*a, **kw):
        pytest.fail('Resume repeated completed native work')

    monkeypatch.setattr('agentcad.inspection_report.BaselineGeometry', unexpected)
    monkeypatch.setattr('agentcad.geometry_images.BaselineGeometry', unexpected)
    invoked, resumed = _invoke(runner, shlex.split(canceled['actions']['resume'])[1:])
    assert invoked.exit_code == 0 and resumed['inspection_job']['status'] == 'complete', resumed
    assert resumed['inspection_job']['id'] == job.job_id
    assert resumed['inspection']['manifest_id'] == saved['manifest_id']
    assert resumed['inspection']['retention']['state'] == 'unmanaged'
    assert len(resumed['inspection']['media']) == 1
    assert _hashes(before) == before
    assert {p: p.stat().st_mtime_ns for p in paths} == mtimes
    assert len(list(isolated_dir.rglob('request.json'))) == 1


def test_id_image_retention_and_mcp_record_agree(runner, isolated_dir):
    step, saved = _baseline(isolated_dir)
    face = next(e['id'] for e in saved['entities'].values() if e['kind'] == 'face')
    result = asyncio.run(server.mcp.call_tool('inspect', {
        'file': str(step), 'cwd': str(isolated_dir), 'entity_id': face, 'image': True,
    }))
    payload = _payload(result)
    assert payload['_exit_code'] == 0 and payload['entity']['id'] == face
    assert payload['image']['request']['object_id'] == face
    assert payload['image']['retention']['scope'] == 'inspection_cache'
    assert sum(block.type == 'image' for block in result.content) == 1
