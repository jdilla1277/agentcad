"""Live documentation reads are independent of CAD credentials and project files."""
import json
import httpx
import pytest
from click.testing import CliRunner
from agentcad.cli import cli
from agentcad.cloud_docs import DocsClient

PAGE={'schema_version':1,'id':'first-cloud-build','title':'Your first cloud build','content':'# Your first cloud build\n\nLive guide.\n','availability':'Preview','url':'https://agentcad.dev/docs/cloud/first-cloud-build'}
INDEX={'schema_version':1,'pages':[{k:PAGE[k] for k in ('id','title','url','availability')}]}

def install(monkeypatch,handler):
 monkeypatch.setattr(DocsClient,'from_env',classmethod(lambda cls: cls('https://docs.test',transport=httpx.MockTransport(handler))))

def test_discovery_and_page_reads_need_no_account_or_project(tmp_path,monkeypatch):
 monkeypatch.chdir(tmp_path)
 monkeypatch.setenv('AGENTCAD_API_KEY','secret-must-not-be-sent')
 monkeypatch.setenv('AGENTCAD_ACCOUNT_STATE',str(tmp_path/'do-not-create'))
 def handler(request):
  assert 'authorization' not in request.headers and 'cookie' not in request.headers
  assert request.method=='GET'
  return httpx.Response(200,json=INDEX if request.url.path=='/api/docs/cloud' else PAGE)
 install(monkeypatch,handler)
 result=CliRunner().invoke(cli,['cloud','docs'])
 assert result.exit_code==0,result.output
 assert json.loads(result.stdout)['pages']==INDEX['pages']
 result=CliRunner().invoke(cli,['cloud','docs','first-cloud-build'])
 assert result.exit_code==0,result.output
 assert json.loads(result.stdout)['content']==PAGE['content']
 result=CliRunner().invoke(cli,['cloud','docs','first-cloud-build','--format','markdown'])
 assert result.exit_code==0,result.output
 assert result.stdout==PAGE['content']
 assert list(tmp_path.iterdir())==[]

def test_reads_fetch_updated_service_content_without_client_changes(monkeypatch):
 calls=[]
 def handler(request):
  calls.append(request.url.path)
  return httpx.Response(200,json={**PAGE,'content':f'# Revision {len(calls)}\n'})
 install(monkeypatch,handler)
 for revision in (1,2):
  result=CliRunner().invoke(cli,['cloud','docs','first-cloud-build'])
  assert json.loads(result.stdout)['content']==f'# Revision {revision}\n'
 assert len(calls)==2

@pytest.mark.parametrize('response,reason',[(httpx.Response(404), 'guide_not_found'),(httpx.Response(503,text='private backend details'),'docs_unavailable'),(httpx.Response(302,headers={'location':'https://other.test'}),'docs_unavailable'),(httpx.Response(200,text='<html>login</html>'),'invalid_docs_response'),(httpx.Response(200,json={'schema_version':99,'pages':[]}),'invalid_docs_response')])
def test_server_errors_are_structured_and_not_success_or_raw_bodies(monkeypatch,response,reason):
 install(monkeypatch,lambda _:response)
 result=CliRunner().invoke(cli,['cloud','docs'])
 assert result.exit_code==1,result.output
 assert json.loads(result.stdout)['reason']==reason
 assert 'private backend' not in result.output

@pytest.mark.parametrize('page',['../secret','first-cloud-build?token=secret','UPPER','a/b'])
def test_invalid_page_never_reaches_network(monkeypatch,page):
 install(monkeypatch,lambda _:pytest.fail('Invalid guide ID sent to service'))
 result=CliRunner().invoke(cli,['cloud','docs',page])
 assert result.exit_code==1
 assert json.loads(result.stdout)['reason']=='invalid_guide'

@pytest.mark.parametrize('url',['http://remote.test','https://user:secret@docs.test','https://docs.test/path','https://docs.test?token=secret'])
def test_origin_validation_precedes_network(monkeypatch,url):
 monkeypatch.setenv('AGENTCAD_DOCS_URL',url)
 result=CliRunner().invoke(cli,['cloud','docs'])
 assert result.exit_code==1
 assert json.loads(result.stdout)['reason']=='invalid_origin'
 assert 'secret' not in result.output


def test_timeout_is_recoverable_without_raw_connection_details(monkeypatch):
 def handler(request):
  raise httpx.ReadTimeout('private connection detail',request=request)
 install(monkeypatch,handler)
 result=CliRunner().invoke(cli,['cloud','docs','first-cloud-build','--format','markdown'])
 assert result.exit_code==1
 assert json.loads(result.stdout)['reason']=='docs_unavailable'
 assert 'private connection' not in result.stdout

@pytest.mark.parametrize('payload',[{'schema_version':1,'pages':[{'id':'../private'}]}, {'schema_version':1,'id':'other','content':'stale'}, {'schema_version':1,'id':'first-cloud-build','content':''}])
def test_invalid_index_or_mismatched_empty_page_fails(monkeypatch,payload):
 install(monkeypatch,lambda _:httpx.Response(200,json=payload))
 args=['cloud','docs']+(['first-cloud-build'] if 'id' in payload else [])
 result=CliRunner().invoke(cli,args)
 assert result.exit_code==1
 assert json.loads(result.stdout)['reason']=='invalid_docs_response'


def test_oversized_reply_is_rejected(monkeypatch):
 install(monkeypatch,lambda _:httpx.Response(200,json={**PAGE,'content':'x'*(1024*1024)}))
 result=CliRunner().invoke(cli,['cloud','docs','first-cloud-build'])
 assert result.exit_code==1
 assert json.loads(result.stdout)['reason']=='invalid_docs_response'
