import asyncio
import json
import httpx
import pytest
from click.testing import CliRunner
from agentcad.cli import cli
from agentcad.cloud import CloudClient, CloudError
from agentcad.mcp import server

TOKEN = 'acad_sk_' + 'd'*24 + '.' + 's'*43
CURSOR = '99999999999999999999_job_' + 'b'*24 + '.json'

def test_cli_and_mcp_read_same_activity_without_local_project(monkeypatch, tmp_path):
    def handle(request):
        assert request.method == 'GET' and request.url.path == '/v1/activity'
        assert request.url.params['limit'] == '2' and request.url.params['cursor'] == CURSOR
        return httpx.Response(200,json={'status':'success','items':[],'next_cursor':None})
    monkeypatch.setattr(CloudClient,'from_env',classmethod(lambda cls:cls('https://api.example.test',None,None,credential=TOKEN,transport=httpx.MockTransport(handle))))
    monkeypatch.chdir(tmp_path)
    response = CliRunner().invoke(cli,['cloud','activity','--limit','2','--cursor',CURSOR])
    assert response.exit_code == 0, response.output
    assert json.loads(response.stdout)['items'] == []
    result = asyncio.run(server.cloud_activity(limit=2,cursor=CURSOR))
    assert result['status'] == 'success' and result['_exit_code'] == 0
    assert list(tmp_path.iterdir()) == []

def test_personal_key_env_is_explicit_and_never_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTCAD_ACCOUNT_STATE", str(tmp_path / "account.json"))
    for name in ['AGENTCAD_API_URL','AGENTCAD_KEY_ID','AGENTCAD_KEY_SECRET']: monkeypatch.delenv(name,raising=False)
    monkeypatch.setenv('AGENTCAD_API_KEY',TOKEN)
    with pytest.raises(CloudError): CloudClient.from_env()
    monkeypatch.setenv('AGENTCAD_API_URL','https://api.example.test')
    with CloudClient.from_env() as client: assert client.http.headers['Authorization'] == 'Bearer '+TOKEN
    monkeypatch.setenv('AGENTCAD_KEY_ID','key_'+'a'*24)
    with pytest.raises(CloudError): CloudClient.from_env()

def test_activity_query_is_bounded_and_next_action_is_safe():
    with CloudClient('https://api.example.test','key_'+'a'*24,'s'*32,transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'status':'success','items':[],'next_cursor':CURSOR}))) as client:
        for values in [{'limit':101},{'limit':True},{'cursor':'../../other'}]:
            with pytest.raises(CloudError): client.activity(**values)
        result = client.activity(limit=2)
        assert result['next_actions'] == ['agentcad cloud activity --limit 2 --cursor '+CURSOR]
        assert result['more_at'] == 'agentcad docs cloud'
