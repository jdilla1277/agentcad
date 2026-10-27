#!/usr/bin/env python3
"""Measure verified ID lookup, CLI and MCP requests without bypassing integrity checks.

Run with PYTHONPATH=src python scripts/benchmark_geometry_lookup.py [files...].
Requires the mcp extra. Fixture setup is excluded from lookup measurements.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter


def _peak_rss_bytes():
    try:
        import resource
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def _request(mode, step, object_id):
    """Prepare entry points before timing; every call still verifies the files."""
    if mode == "verified_path":
        from agentcad.geometry_manifest import read_manifest, record, summary

        def request():
            saved = read_manifest(step)
            return {"entity": record(saved, object_id), "geometry_manifest": summary(step, saved)}

    elif mode == "cli_dispatch":
        from agentcad.cli import cli

        def request():
            output = io.StringIO()
            with redirect_stdout(output):
                cli.main(["inspect", str(step), "--id", object_id, "--no-daemon"], standalone_mode=False)
            return json.loads(output.getvalue())

    elif mode == "mcp_tool":
        from agentcad.mcp.server import inspect

        def request():
            result = inspect(file=str(step), cwd=str(step.parent), entity_id=object_id)
            assert result.pop("_exit_code") == 0, result
            return result

    else:
        raise ValueError(mode)
    return request


def _validate(result, object_id, manifest_id):
    assert result["entity"]["id"] == object_id, result
    assert result["geometry_manifest"]["manifest_id"] == manifest_id, result


def _times(samples):
    return {
        "first_request_s": samples[0],
        "subsequent_median_s": statistics.median(samples[1:]),
        "subsequent_max_s": max(samples[1:]),
        "subsequent_samples": len(samples) - 1,
    }


def _worker(args):
    request = _request(args.worker, args.step, args.object_id)
    samples = []
    for _ in range(args.repeats + 1):
        start = perf_counter()
        result = request()
        samples.append(perf_counter() - start)
        _validate(result, args.object_id, args.manifest_id)
    return {**_times(samples), "peak_process_rss_bytes": _peak_rss_bytes()}


def _measure(step, saved, repeats):
    object_id = next(e["id"] for e in reversed(list(saved["entities"].values())) if e["kind"] == "face")
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "AGENTCAD_DAEMON": "1", "AGENTCAD_NO_LOG": "1",
    }
    modes = {}
    for mode in ("verified_path", "cli_dispatch", "mcp_tool"):
        # Separate processes keep fixture construction and other modes out of
        # each peak RSS. Imports remain included in process memory high water.
        result = subprocess.run([
            sys.executable, str(Path(__file__).resolve()), "--worker", mode,
            "--step", str(step), "--object-id", object_id,
            "--manifest-id", saved["manifest_id"], "--repeats", str(repeats),
        ], cwd=step.parent, env=env, capture_output=True, text=True, encoding="utf-8", check=True, timeout=120)
        modes[mode] = json.loads(result.stdout)
    samples = []
    for _ in range(repeats + 1):
        start = perf_counter()
        result = subprocess.run([
            sys.executable, "-m", "agentcad", "inspect", str(step),
            "--id", object_id, "--no-daemon",
        ], cwd=step.parent, env=env, capture_output=True, text=True, encoding="utf-8", check=True, timeout=120)
        samples.append(perf_counter() - start)
        _validate(json.loads(result.stdout), object_id, saved["manifest_id"])
    modes["cli_fresh_process"] = _times(samples)
    return {
        "counts": saved["counts"], "lookup_id": object_id,
        "step_bytes": step.stat().st_size,
        "manifest_bytes": step.with_name("geometry_manifest.json").stat().st_size,
        "requests": modes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=Path, nargs="*")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--worker", choices=("verified_path", "cli_dispatch", "mcp_tool"), help=argparse.SUPPRESS)
    parser.add_argument("--step", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--object-id", help=argparse.SUPPRESS)
    parser.add_argument("--manifest-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.worker:
        print(json.dumps(_worker(args)))
        return

    from benchmark_step_assembly import _generated_fixture
    from agentcad.geometry_manifest import create_manifest
    from agentcad.step_assembly import read_step_assembly, write_step_assembly

    with tempfile.TemporaryDirectory(prefix="agentcad-lookup-") as temp:
        root = Path(temp)
        fixtures = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "real_world"
        paths = [p.resolve() for p in args.files] if args.files else [
            fixtures / "cadgenbench_101_agentcad.step",
            fixtures / "pump_manifold.step",
            _generated_fixture(root / "repeated_100.step", repeated=True),
            _generated_fixture(root / "repeated_1000.step", repeated=True, count=1000),
            _generated_fixture(root / "high_face_900.step", repeated=False),
        ]
        measurements = []
        for i, path in enumerate(paths):
            directory = root / f"baseline_{i}"
            directory.mkdir()
            step = directory / "output.step"
            document = read_step_assembly(path)
            write_step_assembly(document, step)
            saved = create_manifest(step, 1, source={
                "filename": path.name, "assembly_metadata": "preserved_source_document",
            })
            measurements.append({"fixture": path.name, **_measure(step, saved, args.repeats)})
    print(json.dumps({
        "scope": "read_manifest -> record -> summary; CLI dispatch; MCP tool; fresh CLI processes",
        "timing_note": "Entry-point imports excluded except cli_fresh_process; every request rereads and verifies both files. First request is process-local, not a cold filesystem guarantee.",
        "memory_note": "Each mode has an isolated worker; RSS includes imports and all its requests, not fixture construction. RSS is null where resource is unavailable. Fresh CLI subprocess RSS is not measured.",
        "excluded": ["fixture setup", "import/report generation", "selection-context construction", "MCP transport/client overhead", "daemon routing", "cold filesystem guarantees"],
        "python": platform.python_version(), "platform": platform.platform(),
        "versions": {p: importlib.metadata.version(p) for p in ("build123d", "cadquery-ocp", "mcp")},
        "measurements": measurements,
    }, indent=2))


if __name__ == "__main__":
    main()
