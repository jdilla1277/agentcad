#!/usr/bin/env python3
"""Capture current CLI comparison results for the Milestone 3 fixtures.

The JSON goes to stdout by default. Pass both external paths to include a local
real-world pair without copying those CAD files into the public repository.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from click.testing import CliRunner

import agentcad
from agentcad.cli import cli


DEFAULT_FIXTURE_DIR = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "comparison"
)


def _capture_case(name: str, reference: Path, candidate: Path):
    runner = CliRunner()
    started = time.monotonic()
    result = runner.invoke(
        cli,
        [
            "diff",
            str(reference.resolve()),
            str(candidate.resolve()),
            "--no-daemon",
        ],
    )
    wall_ms = round((time.monotonic() - started) * 1000)
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError:
        response = {
            "command": "diff",
            "status": "error",
            "message": "Baseline command did not return valid JSON.",
            "stdout": result.stdout[-1000:],
            "stderr": result.stderr[-1000:],
        }
    return name, {
        "exit_code": result.exit_code,
        "wall_ms": wall_ms,
        "response": response,
    }


def capture(
    fixture_dir: Path,
    selected_cases: list[str] | None = None,
    external_pair: tuple[Path, Path] | None = None,
):
    catalog = json.loads((fixture_dir / "catalog.json").read_text())
    names = list(catalog["cases"]) if selected_cases is None else selected_cases
    unknown = sorted(set(names) - set(catalog["cases"]))
    if unknown:
        raise ValueError(f"Unknown comparison fixture case: {', '.join(unknown)}")
    cases = {}
    for name in names:
        case = catalog["cases"][name]
        captured_name, result = _capture_case(
            name,
            fixture_dir / case["reference"],
            fixture_dir / case["candidate"],
        )
        cases[captured_name] = result

    if external_pair is not None:
        captured_name, result = _capture_case(
            "external_pair",
            external_pair[0],
            external_pair[1],
        )
        cases[captured_name] = result

    return {
        "schema_version": 1,
        "tool_version": agentcad.__version__,
        "exact_timeout_s": os.environ.get("AGENTCAD_DIFF_TIMEOUT_S", "30"),
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        help="Capture one catalog case; repeat for multiple cases.",
    )
    parser.add_argument("--external-reference", type=Path)
    parser.add_argument("--external-candidate", type=Path)
    parser.add_argument(
        "--external-only",
        action="store_true",
        help="Capture only the supplied external pair, not the public cases.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if bool(args.external_reference) != bool(args.external_candidate):
        parser.error(
            "--external-reference and --external-candidate must be provided together"
        )
    if args.external_only and args.external_reference is None:
        parser.error("--external-only requires an external reference and candidate")
    external_pair = None
    if args.external_reference is not None:
        external_pair = (args.external_reference, args.external_candidate)

    payload = capture(
        args.fixture_dir,
        selected_cases=[] if args.external_only else args.cases,
        external_pair=external_pair,
    )
    encoded = json.dumps(payload, indent=2)
    if args.output is not None:
        args.output.write_text(encoded + "\n")
    else:
        print(encoded)

    failed = any(
        case["exit_code"] != 0 or case["response"].get("status") != "success"
        for case in payload["cases"].values()
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
