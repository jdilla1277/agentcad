import asyncio
import json

import pytest

from agentcad.cli import cli
from agentcad.geometry_query import InvalidQuery, parse_query


@pytest.mark.parametrize("query", [
    {}, {"kind": "feature"}, {"kind": "face", "similar_to": "face_v1_1"},
    {"kind": "edge", "where": {"area": {"min": 1, "units": "mm2"}}},
    {"kind": "face", "where": {"surface_type": "hole"}},
    {"kind": "face", "where": {"area": {"min": 1}}},
    {"kind": "face", "where": {"area": {"min": 2, "max": 1, "units": "mm2"}}},
    {"kind": "face", "where": {"area": {"value": True, "units": "mm2"}}},
    {"kind": "face", "where": {"area": {"value": float("nan"), "units": "mm2"}}},
    {"kind": "face", "where": {"area": {"value": 1e308, "units": "m2"}}},
    {"kind": "face", "where": {"location": {"origin": [1e308, 0, 0], "units": "m", "tolerance": 0}}},
    {"kind": "face", "where": {"orientation": {"direction": [0, 0, 0]}}},
    {"kind": "face", "where": {"plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "units": "mm", "tolerance": 0}}},
    {"kind": "face", "where": {"relation": {"type": "distance", "to": ["face_v1_1"], "distance": {"max": 1, "units": "mm"}}}},
    {"kind": "face", "where": {"any": []}}, {"kind": "face", "result_mode": "definitions"},
    '{"kind":"face","kind":"edge"}',
])
def test_invalid_queries_are_explicit(query):
    with pytest.raises(InvalidQuery):
        parse_query(query)


def test_units_and_composition_normalize_without_relaxing():
    query = parse_query({"kind": "face", "where": {"all": [
        {"area": {"value": 1, "tolerance": .01, "units": "cm2"}},
        {"not": {"surface_type": "plane"}},
    ]}})
    assert query["where"]["all"][0]["area"] == {"value": 100, "tolerance": 1, "units": "mm2"}


def test_large_and_small_nonzero_directions_normalize_safely():
    for magnitude in (1e308, 1e-308):
        query = parse_query({"kind": "face", "where": {"orientation": {"direction": [magnitude, magnitude, 0]}}})
        assert query["where"]["orientation"]["direction"] == pytest.approx([2 ** -.5, 2 ** -.5, 0])


@pytest.mark.parametrize("args", [
    ["--query", '{"kind":"face"}'], ["--search-snapshot", "search_" + "a" * 64],
    ["--cursor", "anything"], ["--search-id", "face_v1_1"],
    ["--search-images", "0"], ["--page-size", "2"], ["--artifact", "search_" + "a" * 64],
])
def test_search_requires_fresh_opt_in_before_io(runner, isolated_dir, monkeypatch, args):
    def forbidden(*a, **k):
        pytest.fail("Read baseline before opt-in")
    monkeypatch.setattr("agentcad.geometry_manifest.read_manifest", forbidden)
    result = runner.invoke(cli, ["inspect", "missing.step", *args, "--no-daemon"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["reason"] == "experimental_geometry_search_required"
    assert not (isolated_dir / ".agentcad").exists()


@pytest.mark.parametrize("value", ["true", 1, None])
def test_mcp_flag_requires_actual_bool(value, isolated_dir):
    from agentcad.mcp.server import mcp
    with pytest.raises(ValueError, match="must be a boolean"):
        asyncio.run(mcp.call_tool("inspect", {"file": "missing.step", "cwd": str(isolated_dir),
            "query": {"kind": "face"}, "experimental_geometry_search": value}))


def test_mcp_search_gate_defaults_off_and_does_not_persist(isolated_dir):
    from agentcad.mcp.server import inspect, mcp
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    assert tools["inspect"].inputSchema["properties"]["experimental_geometry_search"]["default"] is False
    for extra in ({}, {"experimental_geometry_search": True}, {}):
        result = inspect(file="missing.step", cwd=str(isolated_dir), query={"kind": "face"}, search_images=0, **extra)
        if extra:
            assert result["reason"] != "experimental_geometry_search_required"
        else:
            assert result["reason"] == "experimental_geometry_search_required"
