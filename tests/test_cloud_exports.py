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
