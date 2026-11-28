"""Readable labels keep their bytes across client surfaces and retries."""
import asyncio
import json

import httpx
import pytest
from click.testing import CliRunner

from agentcad.cli import cli
from agentcad.cloud import CloudClient, CloudError
from agentcad.mcp import server
from tests.test_cloud_surface import install, PROJECT, JOB

VALID = ['Account journey bracket', 'A', 'a' * 64, 'a' * 62 + ' b', 'a  b', 'legacy_name-2']
INVALID = ['', ' ', ' leading', 'trailing ', '1part', 'a' * 65, 'a/b', 'a\\b', 'a\x00b', 'a\nb', 'a\tb', 'café', 'a.b']

@pytest.mark.parametrize('label', VALID)
def test_label_is_preserved_by_cli_mcp_and_retry(tmp_path, monkeypatch, label):
    script = tmp_path / 'model.py'
    script.write_text('show_object(Box(20,30,5))')
    requests = []
    def handler(request):
        requests.append(request)
        assert ('\r\n\r\n' + label + '\r\n').encode() in request.content
        assert request.headers['Idempotency-Key'] == 'same-label'
        return httpx.Response(202, json={'id': JOB, 'project_id': PROJECT, 'kind': 'run', 'status': 'queued'})
    install(monkeypatch, handler)
    cli_result = CliRunner().invoke(cli, ['cloud', 'run', PROJECT, str(script), '--label', label, '--idempotency-key', 'same-label'])
    assert cli_result.exit_code == 0, cli_result.output
    assert json.loads(cli_result.stdout)['id'] == JOB
    mcp_result = asyncio.run(server.cloud_run(PROJECT, str(script), label, idempotency_key='same-label'))
    assert mcp_result['id'] == JOB
    assert len(requests) == 2

@pytest.mark.parametrize('label', INVALID)
def test_invalid_label_cannot_submit(tmp_path, label):
    with CloudClient('https://api.example.test', 'key_' + 'c' * 24, 'secret-' + 'd' * 32,
                     transport=httpx.MockTransport(lambda _: pytest.fail('Invalid label submitted'))) as api:
        with pytest.raises(CloudError) as caught:
            api.run(PROJECT, 'missing.py', label, cwd=str(tmp_path))
    assert caught.value.reason == 'invalid_label'
