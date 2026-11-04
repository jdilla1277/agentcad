"""CLI and MCP expose the bounded cloud workflow without local project state."""

import json
import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from click.testing import CliRunner

from agentcad.cli import cli
from agentcad.cloud import CloudClient
from agentcad.mcp import server

PROJECT = "prj_" + "a" * 24
JOB = "job_" + "b" * 24
SECRET = "test-secret-" + "d" * 32
CLOUD_TOOLS = {"cloud_create_project", "cloud_projects", "cloud_project", "cloud_run", "cloud_import",
               "cloud_measure", "cloud_inspect", "cloud_check_spec", "cloud_render", "cloud_diff", "cloud_job", "cloud_download"}


def install(monkeypatch, handler):
    monkeypatch.setattr(CloudClient, "from_env", classmethod(lambda cls: cls(
        "https://api.example.test", "key_" + "c" * 24, SECRET, transport=httpx.MockTransport(handler))))


def test_cloud_cli_projects_needs_no_manifest_or_local_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENTCAD_NO_LOG", raising=False)
    install(monkeypatch, lambda request: httpx.Response(200, json={"projects": [{"id": PROJECT}]}))
    response = CliRunner().invoke(cli, ["cloud", "projects", "list"])
    assert response.exit_code == 0, response.output
    result = json.loads(response.stdout)
    assert result["projects"][0]["id"] == PROJECT and result["mode"] == "cloud"
    assert list(tmp_path.iterdir()) == []


def test_cloud_cli_upload_and_generated_retry_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "model.py").write_text("show_object(Box(20,30,5))")
    install(monkeypatch, lambda request: httpx.Response(202, json={
        "id": JOB, "project_id": PROJECT, "kind": "run", "status": "queued"}))
    response = CliRunner().invoke(cli, ["cloud", "run", PROJECT, "model.py", "--label", "first"])
    assert response.exit_code == 0, response.output
    result = json.loads(response.stdout)
    assert result["status"] == "queued" and result["idempotency_key"]
    assert SECRET not in response.output
    assert sorted(path.name for path in tmp_path.iterdir()) == ["model.py"]


def test_cli_and_mcp_complete_job_preserve_null_verdict(monkeypatch):
    def handler(request):
        if request.url.path.endswith("/result"):
            return httpx.Response(200, json={"command": "check-spec", "status": "success", "passed": None,
                                             "geometry_valid": True, "unsupported_requirements": [{"path": "material"}]})
        return httpx.Response(200, json={"id": JOB, "project_id": PROJECT, "status": "succeeded", "kind": "check_spec", "result_url": "/ignored"})
    install(monkeypatch, handler)
    response = CliRunner().invoke(cli, ["cloud", "job", PROJECT, JOB])
    assert response.exit_code == 0, response.output
    assert json.loads(response.stdout)["result"]["passed"] is None
    result = asyncio.run(server.cloud_job(PROJECT, JOB))
    assert result["result"]["passed"] is None and result["_exit_code"] == 0


def test_cloud_mcp_registers_typed_tools_alongside_local_tools():
    tools = {tool.name: tool for tool in server.mcp._tool_manager.list_tools()}
    assert CLOUD_TOOLS <= tools.keys()
    assert {"run", "render", "measure", "inspect", "diff", "check_spec"} <= tools.keys()
    assert {"project", "script", "label"} <= set(tools["cloud_run"].parameters["required"])
    assert "idempotency_key" in tools["cloud_run"].parameters["properties"]
    assert {"project", "version"} <= set(tools["cloud_render"].parameters["required"])
    assert "view" not in tools["cloud_render"].parameters["properties"]
    assert "build_dir" not in tools["cloud_run"].parameters["properties"]


def test_cloud_mcp_missing_file_is_structured_and_does_not_change_cwd(tmp_path, monkeypatch):
    before = Path.cwd()
    install(monkeypatch, lambda _: pytest.fail("Missing file made a request"))
    result = asyncio.run(server.cloud_run(PROJECT, "missing.py", "first", cwd=str(tmp_path)))
    assert result["status"] == "error" and result["_exit_code"] == 1
    assert Path.cwd() == before


def test_cloud_mcp_invalid_base_directory_is_structured(monkeypatch):
    install(monkeypatch, lambda _: pytest.fail("Invalid directory made a request"))
    result = asyncio.run(server.cloud_run(PROJECT, "model.py", "first", cwd="\x00"))
    assert result["reason"] == "invalid_directory" and result["_exit_code"] == 1


def test_cloud_cli_unsupported_local_option_is_json_error():
    result = CliRunner().invoke(cli, ["cloud", "render", PROJECT, "1", "--view", "all"])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["status"] == "error"


def test_cloud_docs_are_discoverable_without_credentials():
    result = CliRunner().invoke(cli, ["docs", "cloud"])
    assert result.exit_code == 0, result.output
    content = json.loads(result.stdout)["content"]
    assert "AGENTCAD_KEY_SECRET" in content and "idempotency" in content.lower()
    assert "cloud_job" in content and "--compare" not in content


def test_mcp_rejects_unsupported_options_before_submitting(monkeypatch):
    install(monkeypatch, lambda _: pytest.fail("Unsupported MCP options submitted work"))
    with pytest.raises(ValueError, match="cloud"):
        asyncio.run(server.mcp.call_tool("cloud_render", {"project": PROJECT, "version": 1, "view": "all"}))


def test_mcp_discovery_declares_closed_cloud_arguments():
    tools = asyncio.run(server.mcp.list_tools())
    assert all(tool.inputSchema.get("additionalProperties") is False for tool in tools if tool.name.startswith("cloud_"))


@pytest.mark.parametrize("operation", ["poll", "download"])
def test_mcp_stays_responsive_during_cloud_io(operation, tmp_path, monkeypatch):
    started = threading.Event()

    def handler(request):
        started.set()
        if operation == "poll":
            return httpx.Response(200, json={"id": JOB, "project_id": PROJECT, "status": "queued"})
        time.sleep(1)
        return httpx.Response(200, content=b"STEP download")

    install(monkeypatch, handler)

    async def check():
        if operation == "poll":
            tool, args = "cloud_job", {"project": PROJECT, "job": JOB, "wait_seconds": 1}
        else:
            tool, args = "cloud_download", {"project": PROJECT, "version": 1, "path": "v1_first/output.step",
                                           "destination": str(tmp_path / "model.step")}
        waiting = asyncio.create_task(server.mcp.call_tool(tool, args))
        try:
            assert await asyncio.to_thread(started.wait, 5), "Cloud request did not start"
            tools = await asyncio.wait_for(server.mcp.list_tools(), timeout=0.5)
            assert CLOUD_TOOLS <= {tool.name for tool in tools}
            assert not waiting.done(), "Cloud I/O blocked discovery until the tool finished"
        finally:
            await waiting

    asyncio.run(check())


@pytest.mark.parametrize("operation", ["run", "import_step", "check_spec", "download"])
@pytest.mark.parametrize("cwd", [None, "inputs"])
def test_cloud_paths_survive_local_tool_chdir(operation, cwd, tmp_path, monkeypatch):
    source, other = tmp_path / "source", tmp_path / "other"
    for directory in (source, other):
        directory.mkdir()
        (directory / "inputs").mkdir()
    selected = source / cwd if cwd else source
    unrelated = other / cwd if cwd else other
    selected.joinpath("model.py").write_text("show_object(Box(11, 12, 13))")
    unrelated.joinpath("model.py").write_text("show_object(Box(99, 99, 99))")
    selected.joinpath("model.step").write_bytes(b"selected STEP")
    unrelated.joinpath("model.step").write_bytes(b"unrelated STEP")
    selected.joinpath("spec.json").write_text('{"expect_solids": 1}')
    unrelated.joinpath("spec.json").write_text('{"expect_solids": 2}')
    monkeypatch.chdir(source)
    started, local_entered, completed = (threading.Event() for _ in range(3))
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(202, json={"id": JOB, "project_id": PROJECT, "kind":
                                           "import" if operation == "import_step" else operation, "status": "queued"})
        if request.url.path.endswith(f"/jobs/{JOB}"):
            return httpx.Response(200, json={"status": "succeeded", "artifact": {
                "project": PROJECT, "version": 1, "path": "v1_first/output.step"}})
        return httpx.Response(200, content=b"downloaded STEP")

    install(monkeypatch, handler)
    original_call = server.cloud_call

    def held_cloud_call(*args, **kwargs):
        started.set()
        try:
            assert local_entered.wait(5), "Local tool did not enter its directory"
            return original_call(*args, **kwargs)
        finally:
            completed.set()

    def local_invoke(self, *args, **kwargs):
        assert Path.cwd() == other
        local_entered.set()
        assert completed.wait(5), "Cloud operation did not finish during the local call"
        return SimpleNamespace(stdout='{"status":"success"}', stderr="", exit_code=0, exception=None)

    monkeypatch.setattr(server, "cloud_call", held_cloud_call)
    monkeypatch.setattr(server.CliRunner, "invoke", local_invoke)

    async def check():
        calls = {
            "run": lambda: server.cloud_run(PROJECT, "model.py", "first", cwd=cwd),
            "import_step": lambda: server.cloud_import(PROJECT, "model.step", cwd=cwd),
            "check_spec": lambda: server.cloud_check_spec(PROJECT, 1, "spec.json", cwd=cwd),
            "download": lambda: server.cloud_download(PROJECT, "model.step", job=JOB, cwd=cwd, overwrite=True),
        }
        waiting = asyncio.create_task(calls[operation]())
        try:
            assert await asyncio.to_thread(started.wait, 5), "Cloud worker did not start"
            assert server._invoke(["docs", "cloud"], cwd=str(other))["status"] == "success"
        finally:
            local_entered.set()
            result = await waiting
        assert result["_exit_code"] == 0, result

    asyncio.run(check())
    assert Path.cwd() == source
    assert requests
    if operation == "run":
        assert b"Box(11, 12, 13)" in requests[0].content and b"Box(99" not in requests[0].content
    elif operation == "import_step":
        assert b"selected STEP" in requests[0].content and b"unrelated STEP" not in requests[0].content
    elif operation == "check_spec":
        assert json.loads(requests[0].content) == {"expect_solids": 1}
    else:
        assert selected.joinpath("model.step").read_bytes() == b"downloaded STEP"
        assert unrelated.joinpath("model.step").read_bytes() == b"unrelated STEP"
