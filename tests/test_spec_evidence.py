"""Failed/stale intent must survive later commands without changing validity."""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from agentcad.cli import cli

QUIET = ['--no-daemon', '--no-preview', '--no-diff', '--no-view']


def invoke(runner, args):
    result = runner.invoke(cli, args, env={'AGENTCAD_DAEMON': '1', 'AGENTCAD_NO_LOG': '1'})
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.fixture
def model(runner, isolated_dir):
    invoke(runner, ['init', '--name', 'spec-evidence', '--runtime', 'build123d', '--no-agent-setup'])
    (isolated_dir / 'plate.py').write_text('plate = Box(60,40,5)\nfor x,y in [(x,y) for x in (-24,-12,0,12,24) for y in (-12,-4,4,12)][:17]:\n    plate = plate - Pos(x,y,0)*Cylinder(1.5,10)\nshow_object(plate, name="plate")\n')
    built = invoke(runner, ['run', 'plate.py', '--label', 'plate', *QUIET])
    return isolated_dir / built['outputs']['step']


def spec(root, count, name='requirements.json'):
    path = root / name
    path.write_text(json.dumps({'features': [{'name':'mounting_holes','type':'cylinder','diameter_mm':3,'count':count}]}))
    return path


def test_nine_vs_seventeen_failure_persists(runner, isolated_dir, model):
    policy = spec(isolated_dir, 9)
    checked = invoke(runner, ['check-spec', str(model), str(policy), '--no-daemon'])
    assert checked['passed'] is False
    assert checked['matched_features'][0]['measured']['count'] == 17
    assert checked['total_abs_count_error'] == 8
    assert checked['metrics']['is_valid'] is True  # artifact health is separate
    assert checked['intent_validation']['status'] == 'fail'
    assert checked['delivery_readiness']['ready'] is False
    assert checked['message'].startswith('Specification check failed')
    meta = json.loads(model.with_name('meta.json').read_text())
    assert meta['intent_validation'] == checked['intent_validation']
    receipt = meta['intent_validation']['checks'][0]
    assert receipt['artifact_sha256'] == hashlib.sha256(model.read_bytes()).hexdigest()
    assert receipt['spec_sha256'] == hashlib.sha256(policy.read_bytes()).hexdigest()
    assert receipt['checked_at'] and receipt['evidence']['passed'] is False
    measured = invoke(runner, ['measure', str(model), '--no-daemon'])
    assert measured['geometry_readiness']['ready'] is True
    assert measured['delivery_readiness']['ready'] is False
    context = invoke(runner, ['context'])
    assert context['artifact_readiness'][0]['delivery_readiness']['ready'] is False


def test_spec_change_stays_stale_until_rechecked(runner, isolated_dir, model):
    policy = spec(isolated_dir, 17)
    checked = invoke(runner, ['check-spec', str(model), str(policy), '--no-daemon'])
    assert checked['delivery_readiness']['ready'] is True
    policy.write_text(policy.read_text() + '\n')
    measured = invoke(runner, ['measure', str(model), '--no-daemon'])
    assert measured['intent_validation']['status'] == 'stale'
    assert measured['delivery_readiness']['ready'] is False
    checked = invoke(runner, ['check-spec', str(model), str(policy), '--no-daemon'])
    assert checked['intent_validation']['status'] == 'pass'
    assert checked['delivery_readiness']['ready'] is True


def test_passing_unrelated_spec_does_not_erase_failure(runner, isolated_dir, model):
    failing = spec(isolated_dir, 9, 'nine.json')
    passing = spec(isolated_dir, 17, 'seventeen.json')
    invoke(runner, ['check-spec', str(model), str(failing), '--no-daemon'])
    checked = invoke(runner, ['check-spec', str(model), str(passing), '--no-daemon'])
    assert checked['passed'] is True
    assert checked['delivery_readiness']['ready'] is False
    assert len(checked['intent_validation']['checks']) == 2
    spec(isolated_dir, 17, 'nine.json')
    checked = invoke(runner, ['check-spec', str(model), str(failing), '--no-daemon'])
    assert checked['delivery_readiness']['ready'] is True


def test_artifact_replacement_invalidates_old_pass(runner, isolated_dir, model):
    policy = spec(isolated_dir, 17)
    invoke(runner, ['check-spec', str(model), str(policy), '--no-daemon'])
    shutil.copy(Path(__file__).parent / 'fixtures/validation/closed_box.step', model)
    measured = invoke(runner, ['measure', str(model), '--no-daemon'])
    assert measured['metrics']['is_valid'] is True
    assert measured['intent_validation']['status'] == 'stale'
    assert measured['delivery_readiness']['ready'] is False


def test_structure_expectation_failure_is_intent_failure(runner, isolated_dir, model):
    policy = isolated_dir / 'structure.json'
    policy.write_text(json.dumps({'expect_solids': 2}))
    checked = invoke(runner, ['check-spec', str(model), str(policy), '--no-daemon'])
    assert checked['passed'] is False
    assert checked['metrics']['is_valid'] is True
    assert checked['validation']['is_valid'] is True
    assert checked['intent_validation']['status'] == 'fail'
    assert checked['delivery_readiness']['ready'] is False


def test_late_optional_metadata_write_cannot_erase_failure(runner, isolated_dir, model):
    from agentcad.core_build import ArtifactLifecycle
    meta_path = model.with_name('meta.json')
    lifecycle = ArtifactLifecycle(meta_path, json.loads(meta_path.read_text()))
    policy = spec(isolated_dir, 9)
    invoke(runner, ['check-spec', str(model), str(policy), '--no-daemon'])
    lifecycle.add_warning('late preview warning')
    saved = json.loads(meta_path.read_text())
    assert saved['intent_validation']['status'] == 'fail'
    assert saved['delivery_readiness']['ready'] is False


def test_interrupted_checker_leaves_declared_requirements_pending(runner, isolated_dir, model, monkeypatch):
    import importlib
    module = importlib.import_module('agentcad.commands.check_spec')
    policy = spec(isolated_dir, 17)
    with monkeypatch.context() as patch:
        def interrupted(*args, **kwargs):
            raise RuntimeError('measurement interrupted')
        patch.setattr(module, 'measure_tier0_payload', interrupted)
        result = runner.invoke(cli, ['check-spec', str(model), str(policy), '--no-daemon'])
        assert result.exit_code != 0
    measured = invoke(runner, ['measure', str(model), '--no-daemon'])
    assert measured['intent_validation']['status'] == 'pending'
    assert measured['delivery_readiness']['ready'] is False


def test_spec_replaced_during_check_cannot_receive_old_pass(runner, isolated_dir, model, monkeypatch):
    import importlib
    module = importlib.import_module('agentcad.commands.check_spec')
    policy = spec(isolated_dir, 17)
    original = module.measure_tier0_payload
    original_bytes = policy.read_bytes()
    def replace(*args, **kwargs):
        result = original(*args, **kwargs)
        spec(isolated_dir, 9)
        return result
    monkeypatch.setattr(module, 'measure_tier0_payload', replace)
    checked = invoke(runner, ['check-spec', str(model), str(policy), '--no-daemon'])
    assert checked['passed'] is True
    assert checked['intent_validation']['status'] == 'stale'
    assert checked['intent_validation']['checks'][0]['spec_sha256'] == hashlib.sha256(original_bytes).hexdigest()
    assert checked['delivery_readiness']['ready'] is False


def test_standalone_check_is_found_from_a_different_working_directory(runner, isolated_dir, model, monkeypatch):
    standalone = isolated_dir / 'external' / 'part.step'
    standalone.parent.mkdir()
    shutil.copy(model, standalone)
    policy = spec(isolated_dir, 9)
    invoke(runner, ['check-spec', str(standalone), str(policy), '--no-daemon'])
    elsewhere = isolated_dir / 'elsewhere'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    measured = invoke(runner, ['measure', str(standalone), '--no-daemon'])
    assert measured['intent_validation']['status'] == 'fail'
    assert measured['delivery_readiness']['ready'] is False


def test_unchecked_required_constraint_is_not_a_pass(runner, isolated_dir, model):
    policy = spec(isolated_dir, 17)
    document = json.loads(policy.read_text())
    document['material'] = 'steel'
    policy.write_text(json.dumps(document))
    checked = invoke(runner, ['check-spec', str(model), str(policy), '--no-daemon'])
    assert checked['supported_passed'] is True
    assert checked['passed'] is None
    assert checked['intent_validation']['status'] == 'pending'
    assert checked['delivery_readiness']['ready'] is False


def test_named_policy_recheck_updates_only_that_policy(runner, isolated_dir, model):
    policy = spec(isolated_dir, 9)
    document = json.loads(policy.read_text())
    document['spec_id'] = 'mounting'
    policy.write_text(json.dumps(document))
    invoke(runner, ['check-spec', str(model), str(policy), '--no-daemon'])
    other_path = isolated_dir / 'renamed.json'
    document['features'][0]['count'] = 17
    other_path.write_text(json.dumps(document))
    checked = invoke(runner, ['check-spec', str(model), str(other_path), '--no-daemon'])
    assert checked['delivery_readiness']['ready'] is True
    assert len(checked['intent_validation']['checks']) == 1


def test_retained_deferred_checks_cannot_look_delivery_ready(runner, isolated_dir):
    invoke(runner, ['init', '--name', 'retained', '--runtime', 'build123d', '--no-agent-setup'])
    (isolated_dir / 'part.py').write_text('show_object(Box(20,20,5))\n')
    policy = isolated_dir / 'checks.json'
    policy.write_text(json.dumps({'schema_version': 1, 'checks': [
        {'check_id': 'length', 'kind': 'dimension', 'axis': 'x', 'expected_mm': 20}]}))
    result = runner.invoke(cli, ['run', '--experimental-changes', 'part.py', '--label', 'pending',
                                 '--checks', str(policy), '--defer-checks', *QUIET])
    built = json.loads(result.stdout)
    assert built['status'] == 'checks_pending', result.output
    assert built['delivery_readiness']['ready'] is False
    measured = invoke(runner, ['measure', built['change']['geometry'], '--no-daemon'])
    assert measured['intent_validation']['status'] == 'pending'
    assert measured['delivery_readiness']['ready'] is False


def test_corrupt_registered_receipt_retains_policies_until_each_is_rechecked(runner, isolated_dir, model):
    from agentcad.intent_validation import _location
    first = spec(isolated_dir, 9)
    second = spec(isolated_dir, 17, 'other.json')
    invoke(runner, ['check-spec', str(model), str(first), '--no-daemon'])
    invoke(runner, ['check-spec', str(model), str(second), '--no-daemon'])
    record = _location(model)[2]
    record.write_text('{broken')
    measured = invoke(runner, ['measure', str(model), '--no-daemon'])
    assert measured['intent_validation']['status'] == 'unavailable'
    assert measured['delivery_readiness']['ready'] is False
    checked = invoke(runner, ['check-spec', str(model), str(second), '--no-daemon'])
    assert checked['passed'] is True and checked['delivery_readiness']['ready'] is False
    first.write_text(second.read_text())
    repaired = invoke(runner, ['check-spec', str(model), str(first), '--no-daemon'])
    assert repaired['delivery_readiness']['ready'] is True


def test_saved_run_retry_exposes_current_spec_blocker(runner, isolated_dir):
    invoke(runner, ['init', '--name', 'retry', '--runtime', 'build123d', '--no-agent-setup'])
    script = isolated_dir / 'part.py'
    script.write_text('show_object(Box(20,20,5))\n')
    args = ['run', '--experimental-changes', 'part.py', '--label', 'retry', '--idempotency-key', 'once', *QUIET]
    built = invoke(runner, args)
    step = isolated_dir / built['outputs']['step']
    policy = isolated_dir / 'solids.json'
    policy.write_text(json.dumps({'expect_solids': 2}))
    invoke(runner, ['check-spec', str(step), str(policy), '--no-daemon'])
    script.write_text('raise RuntimeError("must not run again")\n')
    replay = invoke(runner, ['run', '--experimental-changes', '--idempotency-key', 'once', '--status', '--no-daemon'])
    assert replay['delivery_readiness']['ready'] is False
    assert replay['intent_validation']['status'] == 'fail'
    assert replay['original_delivery_readiness']['ready'] is True
    assert len(json.loads((isolated_dir / 'agentcad.json').read_text())['versions']) == 1


@pytest.mark.parametrize('newer_status', ['fail', 'pending'])
def test_older_check_cannot_replace_newer_named_policy(runner, isolated_dir, model, monkeypatch, newer_status):
    from click.testing import CliRunner
    import importlib
    command = importlib.import_module('agentcad.commands.check_spec')
    first = isolated_dir / 'older.json'
    first.write_text(json.dumps({'spec_id': 'structure', 'expect_solids': 1}))
    second = isolated_dir / 'newer.json'
    newer_spec = {'spec_id': 'structure', 'expect_solids': 2 if newer_status == 'fail' else 1}
    if newer_status == 'pending':
        newer_spec['material'] = 'steel'
    second.write_text(json.dumps(newer_spec))
    original = command.measure_tier0_payload
    paused = False
    def finish_newer_before_older(*args, **kwargs):
        nonlocal paused
        measured = original(*args, **kwargs)
        if not paused:
            paused = True
            newer = invoke(CliRunner(), ['check-spec', str(model), str(second), '--no-daemon'])
            assert newer['intent_validation']['status'] == newer_status
        return measured
    monkeypatch.setattr(command, 'measure_tier0_payload', finish_newer_before_older)
    older = invoke(runner, ['check-spec', str(model), str(first), '--no-daemon'])
    assert older['passed'] is True  # Its own comparison remains historical.
    assert older['superseded'] is True
    assert 'superseded' in older['message']
    assert any(str(second) in action for action in older['next_actions'])
    assert not any('completed model' in action for action in older['next_actions'])
    for result in (older, invoke(runner, ['measure', str(model), '--no-daemon']),
                   json.loads(model.with_name('meta.json').read_text())):
        assert result['geometry_readiness']['ready'] is True
        assert result['intent_validation']['status'] == newer_status
        assert result['delivery_readiness']['ready'] is False
        receipt = result['intent_validation']['checks'][0]
        assert receipt['spec'] == newer_spec
        assert receipt['spec_file'] == str(second.resolve())


def test_recovered_mirror_cannot_authorize_an_inflight_checker(runner, isolated_dir, model, monkeypatch):
    import importlib
    from agentcad.intent_validation import _location
    command = importlib.import_module('agentcad.commands.check_spec')
    policy = spec(isolated_dir, 17)
    original = command.measure_tier0_payload
    def corrupt_after_measurement(*args, **kwargs):
        measured = original(*args, **kwargs)
        _location(model)[2].write_text('{broken')
        return measured
    with monkeypatch.context() as patch:
        patch.setattr(command, 'measure_tier0_payload', corrupt_after_measurement)
        interrupted = invoke(runner, ['check-spec', str(model), str(policy), '--no-daemon'])
    assert interrupted['passed'] is True
    assert interrupted['intent_validation']['status'] == 'unavailable'
    assert interrupted['delivery_readiness']['ready'] is False
    fresh = invoke(runner, ['check-spec', str(model), str(policy), '--no-daemon'])
    assert fresh['delivery_readiness']['ready'] is True
