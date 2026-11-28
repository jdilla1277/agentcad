"""Hosted exports preserve retry identity and never trust returned URLs."""

import asyncio
import hashlib
import json

import httpx
import pytest
from click.testing import CliRunner

from agentcad.cli import cli
from agentcad.cloud import CloudError
from agentcad.mcp import server
from tests.test_cloud import JOB, PROJECT, ROOT, client, queued
from tests.test_cloud_surface import install


def test_export_uses_saved_version_and_canonical_formats():
    calls = []
    def handle(request):
        calls.append(request)
        assert request.method == "POST" and request.url.path == ROOT + "/versions/2/exports"
        assert json.loads(request.content) == {"formats": ["glb", "obj", "stl"]}
        assert request.headers["Idempotency-Key"] == "export-v2"
        return httpx.Response(202, json=queued("export"))
    with client(handle) as api:
        result = api.export(PROJECT, 2, ["stl", "obj", "glb"], idempotency_key="export-v2")
    assert result["idempotency_key"] == "export-v2" and len(calls) == 1


@pytest.mark.parametrize("formats", [[], ["step"], ["stl", "stl"], ["../secret"], "stl", [None]])
def test_invalid_formats_never_submit(formats):
    with client(lambda _: pytest.fail("Invalid export made a request")) as api, pytest.raises(CloudError):
        api.export(PROJECT, 1, formats)


@pytest.mark.parametrize("fmt", ["stl", "obj", "glb"])
def test_download_written_mesh_uses_fixed_private_route(fmt, tmp_path):
    data = b"mesh-file-bytes"
    def handle(request):
        assert request.url.path == ROOT + f"/jobs/{JOB}/artifacts/output.{fmt}"
        return httpx.Response(200, content=data)
    destination = tmp_path / f"download.{fmt}"
    with client(handle) as api:
        result = api.download(PROJECT, str(destination), job=JOB, kind=fmt)
        assert destination.read_bytes() == data
        assert result["sha256"] == hashlib.sha256(data).hexdigest()
        with pytest.raises(CloudError):
            api.download(PROJECT, str(destination), job=JOB, kind=fmt)
    assert result["kind"] == fmt


def test_cli_and_local_mcp_export_and_download(tmp_path, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == "POST":
            assert json.loads(request.content) == {"formats": ["glb", "stl"]}
            return httpx.Response(202, json=queued("export"))
        return httpx.Response(200, content=b"downloaded mesh")
    install(monkeypatch, handler)
    response = CliRunner().invoke(cli, ["cloud", "export", PROJECT, "2", "--format", "stl,glb", "--idempotency-key", "first"])
    assert response.exit_code == 0, response.output
    assert json.loads(response.stdout)["kind"] == "export"
    response = asyncio.run(server.cloud_export(PROJECT, 2, ["stl", "glb"], idempotency_key="second"))
    assert response["kind"] == "export" and response["_exit_code"] == 0
    destination = tmp_path / "cloud.glb"
    response = CliRunner().invoke(cli, ["cloud", "download", PROJECT, str(destination), "--job", JOB, "--kind", "glb"])
    assert response.exit_code == 0, response.output
    assert destination.read_bytes() == b"downloaded mesh"
    assert len(calls) == 3


def test_mesh_reference_requires_a_job_before_any_http_request(tmp_path):
    with client(lambda _: pytest.fail("Invalid mesh reference made a request")) as api, pytest.raises(CloudError):
        api.download(PROJECT, str(tmp_path / "output"), kind="stl", version=1, path="v1_model/output.stl")


@pytest.mark.parametrize("expired", [True, False])
def test_wait_deadline_read_timeout_preserves_only_an_authorized_pending_snapshot(monkeypatch, expired):
    clock, requests = [0.0], []
    monkeypatch.setattr("agentcad.cloud.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("agentcad.cloud.time.sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json=queued("export"))
        clock[0] = 3.01 if expired else 2.01
        raise httpx.ReadTimeout("later poll timed out", request=request)
    with client(handle) as api:
        if expired:
            result = api.job(PROJECT, JOB, wait_seconds=3)
            assert result["status"] == "queued" and result["next_actions"]
        else:
            with pytest.raises(CloudError, match="response was not received"):
                api.job(PROJECT, JOB, wait_seconds=3)
    assert len(requests) == 2 and all(request.method == "GET" for request in requests)


def test_first_poll_failure_never_returns_an_invented_pending_snapshot():
    def handle(request):
        raise httpx.ReadTimeout("no authorized snapshot", request=request)
    with client(handle) as api, pytest.raises(CloudError):
        api.job(PROJECT, JOB, wait_seconds=1)


@pytest.mark.parametrize("status", [401, 404, 503])
def test_wait_deadline_never_hides_an_explicit_http_error(monkeypatch, status):
    clock, calls = [0.0], []
    monkeypatch.setattr("agentcad.cloud.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("agentcad.cloud.time.sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json=queued("export"))
        clock[0] = 3.01
        return httpx.Response(status, json={"detail": {"reason": "request_failed", "message": "Rejected"}})
    with client(handle) as api, pytest.raises(CloudError) as caught:
        api.job(PROJECT, JOB, wait_seconds=3)
    assert caught.value.details["http_status"] == status


def test_connected_cli_can_export_with_bearer_auth_and_client_attribution():
    from agentcad import __version__
    from agentcad.cloud import CloudClient
    credential = "acli_12345678-1234-4234-8234-123456789abc." + "s" * 43
    def handle(request):
        assert request.headers["Authorization"] == "Bearer " + credential
        assert "X-AgentCAD-Key-ID" not in request.headers
        assert "X-AgentCAD-Key-Secret" not in request.headers
        assert request.headers["X-AgentCAD-Client-Version"] == __version__
        assert request.headers["X-AgentCAD-Client-Mode"] == "api"
        assert request.headers["X-AgentCAD-Client-Source"] == "api"
        return httpx.Response(202, json=queued("export"))
    with CloudClient("https://api.example.test", None, None, credential=credential,
                     transport=httpx.MockTransport(handle)) as api:
        assert api.export(PROJECT, 1, ["glb"])["kind"] == "export"


def test_cloud_run_can_request_build_time_glb(tmp_path):
    script = tmp_path / "model.py"
    script.write_text("show_object(Box(10,20,5))")
    def handle(request):
        assert b'name="export_glb"' in request.content
        assert b'\r\ntrue\r\n' in request.content
        return httpx.Response(202, json={**queued("run"), "export_glb": True})
    with client(handle) as api:
        assert api.run(PROJECT, str(script), "first", export_glb=True)["kind"] == "run"


def test_cli_and_mcp_expose_build_time_glb(tmp_path, monkeypatch):
    script = tmp_path / "model.py"
    script.write_text("show_object(Box(10,20,5))")
    def handle(request):
        assert b'name="export_glb"' in request.content
        return httpx.Response(202, json={**queued("run"), "export_glb": True})
    install(monkeypatch, handle)
    result = CliRunner().invoke(cli, ["cloud", "run", PROJECT, str(script), "--label", "first", "--export", "glb"])
    assert result.exit_code == 0, result.output
    result = asyncio.run(server.cloud_run(PROJECT, str(script), "first", export_glb=True))
    assert result["kind"] == "run"


def test_older_host_cannot_silently_ignore_requested_build_glb(tmp_path):
    script = tmp_path / "model.py"
    script.write_text("show_object(Box(10,20,5))")
    with client(lambda _: httpx.Response(202, json=queued("run"))) as api, pytest.raises(CloudError) as caught:
        api.run(PROJECT, str(script), "first", export_glb=True, idempotency_key="keep-this-build")
    assert caught.value.reason == "build_export_unconfirmed"
    assert caught.value.details["job"]["id"] == JOB
    assert caught.value.details["idempotency_key"] == "keep-this-build"
