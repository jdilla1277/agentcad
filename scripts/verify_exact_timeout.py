#!/usr/bin/env python3
"""Development harness proving exact comparison workers are terminated."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from agentcad.cli import cli


SCRIPT = """\
from build123d import Box
show_object(Box({size}, 10, 10))
"""


def _invoke_json(runner: CliRunner, args: list[str]) -> tuple[object, dict]:
    result = runner.invoke(cli, args)
    if not result.stdout:
        raise RuntimeError(result.exception or "command returned no JSON")
    return result, json.loads(result.stdout)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agentcad-exact-timeout-") as temp:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=temp):
            project = Path.cwd()
            script = project / "verify.py"
            marker = project / "worker-finished"
            script.write_text(SCRIPT.format(size=10))
            init, _ = _invoke_json(runner, [
                "init", "--name", "exact-timeout-verification",
                "--runtime", "build123d",
            ])
            first, _ = _invoke_json(runner, [
                "run", "verify.py", "--output", "first",
                "--no-preview", "--no-view", "--no-daemon",
            ])

            script.write_text(SCRIPT.format(size=12))
            sleeper = (
                "import pathlib,time; "
                "time.sleep(5); "
                f"pathlib.Path({str(marker)!r}).write_text('not killed')"
            )
            started = time.monotonic()
            with patch.dict(
                os.environ, {"AGENTCAD_DIFF_TIMEOUT_S": "0.05"}
            ), patch(
                "agentcad.solid_compare._exact_worker_argv",
                return_value=[sys.executable, "-c", sleeper],
            ):
                second, response = _invoke_json(runner, [
                    "run", "verify.py", "--output", "bounded",
                    "--no-preview", "--no-view", "--no-daemon",
                ])
            elapsed_s = time.monotonic() - started

            context_result, context = _invoke_json(runner, ["context"])
            phases = response.get("comparison_phases", {})
            exact = response.get("diff", {}).get("comparison_3d", {})
            exact_duration_ms = phases.get("exact_3d_comparison", {}).get(
                "duration_ms", 5000
            )
            version_dir = project / "v2_bounded"
            checks = {
                "commands_exit_zero": (
                    init.exit_code == 0
                    and first.exit_code == 0
                    and second.exit_code == 0
                ),
                "run_succeeds": response.get("status") == "success",
                "projection_survives": (
                    phases.get("projection_comparison", {}).get("status")
                    == "success"
                ),
                "exact_phase_times_out": (
                    phases.get("exact_3d_comparison", {}).get("status")
                    == "timeout"
                    and exact.get("status") == "timeout"
                    and exact.get("timeout_s") == 0.05
                    and exact.get("reason", {}).get("code")
                    == "exact_comparison_timeout"
                ),
                "difference_export_is_skipped": (
                    phases.get("difference_artifact_export", {}).get("status")
                    == "skipped"
                ),
                "worker_was_terminated": not marker.exists(),
                "exact_deadline_stops_sleeper": exact_duration_ms < 5000,
                "version_is_committed": (
                    context_result.exit_code == 0
                    and context.get("version_count") == 2
                    and context.get("current") == "bounded"
                    and (version_dir / "output.step").exists()
                    and (version_dir / "meta.json").exists()
                ),
            }
            summary = {
                "status": "success" if all(checks.values()) else "failed",
                "checks": checks,
                "elapsed_s": round(elapsed_s, 3),
                "exact_duration_ms": exact_duration_ms,
                "comparison_phases": phases,
                "comparison_3d": exact,
            }
            print(json.dumps(summary, indent=2))
            return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
