"""Authored compound interference survives real execution, metadata and MCP."""
# collect-on-default-profile
import importlib.util
import json
import math
from pathlib import Path

import pytest
from agentcad.cli import cli
from agentcad.mcp.server import _invoke

FIXTURES = Path(__file__).parent / 'fixtures/part_contacts'


@pytest.mark.parametrize('runtime',['build123d','cadquery'])
def test_real_compound_body_and_embedded_annular_boss(runner,isolated_dir,runtime):
    if runtime == 'cadquery' and importlib.util.find_spec('cadquery') is None:
        pytest.skip('Optional CadQuery extra')
    def invoke(args):
        response = runner.invoke(cli,args)
        assert response.exit_code == 0,response.output
        return json.loads(response.stdout)
    invoke(['init','--runtime',runtime,'--no-agent-setup'])
    (isolated_dir/'model.py').write_bytes((FIXTURES/f'{runtime}.py').read_bytes())
    built = invoke(['run','model.py','--label','boss','--no-daemon','--no-view','--no-preview','--no-diff'])
    assert built['metrics']['is_valid'] is True
    assert built['delivery_readiness']['ready'] is True  # Interference is advisory.
    step = isolated_dir/built['outputs']['step']
    metadata = json.loads(step.with_name('meta.json').read_text())
    listing = _invoke(['parts','list','1'],cwd=str(isolated_dir))
    for result in (built,metadata,listing):
        body,boss,spacer = result['parts']
        assert body['connection']['status'] == boss['connection']['status'] == 'overlapping'
        assert body['connection']['overlap_check']['status'] == 'complete'
        overlap = body['connection']['overlap_check']['overlaps'][0]
        assert overlap['part_id'] == 'boss'
        assert overlap['volume_mm3'] == pytest.approx(math.pi*(8**2-4**2)*2,abs=1e-5)
        assert spacer['connection']['status'] == 'floating'
    assert any('overlaps material' in w for w in built['warnings'])
