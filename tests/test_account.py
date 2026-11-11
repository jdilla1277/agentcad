"""A CLI installation starts untrusted and only gains cloud access after approval."""
import hashlib
import json
import os
import stat
from urllib.parse import urlsplit

import httpx
import pytest

from agentcad.account import AccountClient, AccountError, AccountState
from agentcad.cloud import CloudClient


def test_connection_retries_and_lost_exchange_preserve_pending_state(tmp_path):
    state = AccountState(tmp_path / "profile")
    requests = []
    approved = False
    lost = False
    def handle(request):
        nonlocal lost
        requests.append(request)
        body = json.loads(request.content) if request.content else {}
        if request.url.path.endswith("/requests"):
            assert "secret" not in body
            return httpx.Response(200, json={"id": body["id"], "state": "approval_pending"})
        if request.url.path.endswith("/exchange"):
            assert hashlib.sha256(body["secret"].encode()).hexdigest() == json.loads(requests[0].content)["challenge"]
            if approved and not lost:
                lost = True
                raise httpx.ReadTimeout("secret must not escape", request=request)
            return httpx.Response(200, json={"state": "connected" if approved else "approval_pending", "workspace_id": "wsp_" + "a" * 24, "api_url": "https://api.example.test"})
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"state": "connected", "workspace_id": "wsp_" + "a" * 24, "api_url": "https://api.example.test"})
        raise AssertionError(request.url)
    with AccountClient(state, site="https://accounts.example.test", transport=httpx.MockTransport(handle)) as client:
        first = client.connect("Laptop")
        again = client.connect("Laptop")
        assert first["approval_url"] == again["approval_url"]
        assert first["connection"] == "approval_pending"
        assert "secret" not in json.dumps(first)
        assert client.status()["connection"] == "approval_pending"
        approved = True
        with pytest.raises(AccountError):
            client.status()
        assert state.load()["pending"]
        assert client.status()["connection"] == "connected"
        assert client.status()["workspace_id"].startswith("wsp_")
    assert stat.S_IMODE((tmp_path / "profile").stat().st_mode) == 0o600
    assert "acli_" in json.dumps(state.load())


def test_origin_binding_prevents_sending_credentials_to_another_host(tmp_path, monkeypatch):
    state = AccountState(tmp_path / "profile")
    state.save({"site": "https://accounts.example.test", "api_url": "https://api.example.test", "credential": "acli_" + "a" * 36 + "." + "s" * 43})
    with pytest.raises(AccountError):
        AccountClient(state, site="https://other.example.test")
    monkeypatch.setenv("AGENTCAD_ACCOUNT_STATE", str(state.path))
    monkeypatch.setenv("AGENTCAD_API_URL", "https://other.example.test")
    monkeypatch.delenv("AGENTCAD_KEY_ID", raising=False)
    monkeypatch.delenv("AGENTCAD_KEY_SECRET", raising=False)
    from agentcad.cloud import CloudError
    with pytest.raises(CloudError):
        CloudClient.from_env()


def test_private_state_rejects_symlinks_and_permissive_files(tmp_path):
    target = tmp_path / "target"; target.write_text('{}'); target.chmod(0o600)
    link = tmp_path / "link"; link.symlink_to(target)
    with pytest.raises(AccountError):
        AccountState(link).load()
    target.chmod(0o644)
    with pytest.raises(AccountError):
        AccountState(target).load()


def test_cloud_client_bearer_never_falls_back_to_pilot_headers():
    credential = "acli_12345678-1234-4234-8234-123456789abc." + "s" * 43
    def handle(request):
        assert request.headers["Authorization"] == "Bearer " + credential
        assert "X-AgentCAD-Key-Secret" not in request.headers
        return httpx.Response(200, json={"projects": []})
    with CloudClient("https://api.example.test", None, None, credential=credential, transport=httpx.MockTransport(handle)) as client:
        assert client.projects() == {"projects": []}


def test_parallel_account_commands_cannot_overwrite_the_same_pending_profile(tmp_path):
    state = AccountState(tmp_path / "profile")
    with AccountClient(state, site="https://account.example.test"):
        with pytest.raises(AccountError, match="Another account command"):
            with AccountClient(state, site="https://account.example.test"):
                pass


def test_cli_reports_invalid_state_as_json_without_creating_project_files(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from agentcad.cli import cli
    state = tmp_path / "profile"
    state.write_text(json.dumps({"pending": {"secret": "never-print-this-secret"}})); state.chmod(0o600)
    monkeypatch.setenv("AGENTCAD_ACCOUNT_STATE", str(state))
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["account", "status"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["reason"] == "invalid_state"
    assert "never-print-this-secret" not in result.stdout
    assert not (tmp_path / "agentcad.toml").exists()
    assert not (tmp_path / ".agentcad").exists()
