"""Real model inventories and edit geometry cross CLI/MCP as checked bytes."""

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agentcad.cli import cli
from agentcad.geometry_manifest import load_geometry
from agentcad.inspection_delivery import estimate, RESERVE
from agentcad.mcp import server
from tests_b3d.test_inspection_report import _baseline, _distinct_parts
from tests_b3d.test_step_assembly import _nested_step

REAL = Path(__file__).parents[1] / "tests/fixtures/real_world/cadgenbench_101_agentcad.step"


def invoke(runner, args):
    result = runner.invoke(cli, [*args, "--no-daemon"])
    assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)


def unpack(result):
    if hasattr(result, "structuredContent") and result.structuredContent:
        return result.structuredContent
    if isinstance(result, dict):
        return result
    if isinstance(result, list):
        return json.loads(result[0].text)
    return json.loads(result.content[0].text)


def cli_bytes(runner, args):
    chunks = bytearray()
    first = None
    while args:
        data = invoke(runner, args)
        item = data["artifact"]
        first = first or item
        assert item["offset"] == len(chunks) and item["sha256"] == first["sha256"]
        chunks.extend(base64.b64decode(item["data"]))
        args = shlex.split(data["next"]["cli"])[1:] if data["next"] else None
    assert item["complete"] and len(chunks) == first["total_bytes"]
    assert hashlib.sha256(chunks).hexdigest() == first["sha256"]
    return bytes(chunks)


@pytest.mark.parametrize("kind", ["real", "assembly"])
def test_cli_mcp_complete_inventory_and_reloaded_selectors(runner, isolated_dir, monkeypatch, kind):
    fixture = REAL if kind == "real" else _nested_step(isolated_dir / "supplier.step", tilted=True)
    step, saved = _baseline(isolated_dir, fixture)
    report = invoke(runner, ["inspect", str(step), "--report"])
    assert estimate(report) + RESERVE <= 2000
    inventory = report["inspection"]["inventory"]
    assert inventory["retrieve"] == {"artifact": "manifest"}
    original = step.read_bytes(), step.with_name("geometry_manifest.json").read_bytes()
    # Retrieve through both adapters after disabling every native preparation path.
    def no_work(*args, **kwargs):
        pytest.fail("saved artifact retrieval ran native CAD/rendering work")
    monkeypatch.setattr("agentcad.geometry_manifest.read_manifest", no_work)
    monkeypatch.setattr("agentcad.inspection_report._analysis", no_work)
    monkeypatch.setattr("agentcad.geometry_images.geometry_image", no_work)
    manifest = cli_bytes(runner, ["inspect", str(step), "--artifact", inventory["retrieve"]["artifact"], "--artifact-bytes", "49152"])
    assert manifest == original[1]
    assert set(json.loads(manifest)["entities"]) == set(saved["entities"])
    response = asyncio.run(server.mcp.call_tool("inspect", {"file": str(step), "cwd": str(isolated_dir), "artifact": "manifest"}))
    first = unpack(response)
    cli_first = invoke(runner, ["inspect", str(step), "--artifact", "manifest"])
    assert {k: v for k, v in first.items() if k != "_exit_code"} == cli_first
    baseline = cli_bytes(runner, ["inspect", str(step), "--artifact", "baseline", "--artifact-bytes", "49152"])
    assert baseline == original[0]
    analysis = cli_bytes(runner, ["inspect", str(step), "--artifact", inventory["report"], "--artifact-bytes", "49152"])
    assert json.loads(analysis)["manifest_id"] == saved["manifest_id"]
    monkeypatch.undo()
    copied = isolated_dir / "client copy"
    copied.mkdir()
    (copied / step.name).write_bytes(baseline)
    (copied / "geometry_manifest.json").write_bytes(manifest)
    geometry = load_geometry(copied / step.name)
    target = next(e for e in saved["entities"].values() if e["kind"] == "face" and len(e["occurrence_ids"]) == 1)
    assert geometry.record(target["id"])["id"] == target["id"]
    assert geometry.pick_face(target["id"]).area == pytest.approx(target["area"], rel=1e-5)
    assert (step.read_bytes(), step.with_name("geometry_manifest.json").read_bytes()) == original


def test_default_import_retrieval_cta_configured_root_child_cwd(runner, isolated_dir, monkeypatch):
    root = isolated_dir / "supplier's géométrie"
    root.mkdir()
    monkeypatch.chdir(root)
    (root / "agentcad.toml").write_text('build_dir="saved CAD"\n', encoding="utf-8")
    imported = invoke(runner, ["import", str(REAL), "--init", "--no-view", "--no-diff"])
    assert imported["core"]["status"] == "success"
    assert estimate(imported) + RESERVE <= 2000
    hint = imported["inspection"]["inventory"]["retrieve"]
    data = invoke(runner, ["inspect", imported["outputs"]["step"], "--artifact", hint["artifact"], "--build-dir", imported["build_root"]])
    assert data["manifest_id"] == imported["geometry_manifest"]["manifest_id"]
    child = root / "child caller"
    child.mkdir()
    monkeypatch.chdir(child)
    continued = invoke(runner, shlex.split(data["next"]["cli"])[1:])
    assert continued["artifact"]["offset"] == data["artifact"]["returned_bytes"]
    assert continued["manifest_id"] == data["manifest_id"]


@pytest.mark.timeout(120)
def test_actual_stdio_round_trip_without_client_reading_server_paths(runner, isolated_dir):
    server_root = isolated_dir / "server geometry"
    server_root.mkdir()
    step, saved = _baseline(server_root, _nested_step(server_root / "supplier.step", tilted=True))
    report = invoke(runner, ["inspect", str(step), "--report", "--full-response"])
    inventory = report["inventory"]
    media = report["media"][0]
    image_ref = str(Path(inventory["image_root"]) / media["png"])
    # Ground truth is captured before the client session. During the session the
    # client handles only protocol messages, never opens a returned server path.
    expected = {"baseline": hashlib.sha256(step.read_bytes()).hexdigest(),
                "manifest": hashlib.sha256(step.with_name("geometry_manifest.json").read_bytes()).hexdigest(),
                inventory["report"]: hashlib.sha256(Path(inventory["report"]).read_bytes()).hexdigest(),
                inventory["bundle"]: hashlib.sha256(Path(inventory["bundle"]).read_bytes()).hexdigest(),
                image_ref: media["sha256"],
                str(Path(image_ref).with_suffix(".json")): hashlib.sha256(Path(image_ref).with_suffix(".json").read_bytes()).hexdigest()}
    client_root = isolated_dir / "separate client"
    client_root.mkdir()
    source_root = Path(__file__).parents[1] / "src"
    params = StdioServerParameters(command=sys.executable, args=["-m", "agentcad.mcp"], cwd=server_root,
                                   env={**os.environ, "PYTHONPATH": str(source_root), "AGENTCAD_NO_LOG": "1", "AGENTCAD_DAEMON": "1"})

    async def workflow():
        received = {}
        with (isolated_dir / "server-stderr.log").open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    inspect = next(t for t in tools.tools if t.name == "inspect")
                    assert "artifact" in inspect.inputSchema["properties"]
                    for reference, digest in expected.items():
                        args = {"file": str(step), "cwd": str(isolated_dir), "artifact": reference, "artifact_bytes": 49152}
                        raw = bytearray()
                        while args:
                            result = await session.call_tool("inspect", args)
                            assert not result.isError
                            data = unpack(result)
                            assert data["status"] == "success", data
                            assert data["manifest_id"] == saved["manifest_id"]
                            item = data["artifact"]
                            assert item["offset"] == len(raw) and item["sha256"] == digest
                            raw.extend(base64.b64decode(item["data"]))
                            args = data["next"]["mcp"]["arguments"] if data["next"] else None
                        assert len(raw) == item["total_bytes"]
                        assert hashlib.sha256(raw).hexdigest() == digest
                        received[reference] = bytes(raw)
        return received

    received = asyncio.run(workflow())
    (client_root / "output.step").write_bytes(received["baseline"])
    (client_root / "geometry_manifest.json").write_bytes(received["manifest"])
    ids = json.loads(received["manifest"])["entities"]
    geometry = load_geometry(client_root / "output.step")
    right = next(e for e in ids.values() if e.get("name") == "right instance")
    target = right["members"]["face"][-1]
    assert geometry.pick_face(target).area == pytest.approx(ids[target]["area"], rel=1e-5)
    assert json.loads(received[inventory["report"]])["counts"] == saved["counts"]
    assert received[image_ref].startswith(b"\x89PNG\r\n\x1a\n")
