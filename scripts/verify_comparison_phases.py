#!/usr/bin/env python3
"""Development harness for observable comparison phase reporting."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from agentcad.cli import cli
from agentcad.comparison_phases import COMPARISON_PHASES


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
    with tempfile.TemporaryDirectory(prefix="agentcad-phases-") as temp:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=temp):
            project = Path.cwd()
            script = project / "verify.py"
            script.write_text(SCRIPT.format(size=10))
            init, _ = _invoke_json(runner, [
                "init", "--name", "comparison-phase-verification",
                "--runtime", "build123d",
            ])
            if init.exit_code != 0:
                raise RuntimeError(init.output)

            first, _ = _invoke_json(runner, [
                "run", "verify.py", "--output", "first",
                "--no-preview", "--no-view", "--no-daemon",
            ])
            script.write_text(SCRIPT.format(size=12))
            second, normal = _invoke_json(runner, [
                "run", "verify.py", "--output", "normal",
                "--no-preview", "--no-view", "--no-daemon",
            ])
            normal_meta = json.loads(
                (project / "v2_normal" / "meta.json").read_text()
            )

            script.write_text(SCRIPT.format(size=14))
            with patch(
                "agentcad.solid_compare.compare_solid_volumes",
                side_effect=RuntimeError("injected exact phase failure"),
            ):
                failed_exact_result, failed_exact = _invoke_json(runner, [
                    "run", "verify.py", "--output", "failed_exact",
                    "--no-preview", "--no-view", "--no-daemon",
                ])

            context_result, context = _invoke_json(runner, ["context"])
            explicit_result, explicit = _invoke_json(runner, [
                "diff", "1", "2", "--no-daemon",
            ])

            normal_phases = normal.get("comparison_phases", {})
            failed_phases = failed_exact.get("comparison_phases", {})
            explicit_phases = explicit.get("comparison_phases", {})
            timings = normal.get("timings", {})
            attempted = [
                (name, entry.get("duration_ms", 0))
                for name, entry in normal_phases.items()
                if "duration_ms" in entry
            ]
            expensive_first = sorted(
                attempted, key=lambda item: item[1], reverse=True
            )

            checks = {
                "normal_runs_exit_zero": (
                    first.exit_code == 0 and second.exit_code == 0
                ),
                "stable_phase_order": (
                    tuple(normal_phases) == COMPARISON_PHASES
                ),
                "normal_phases_all_succeed": all(
                    normal_phases.get(name, {}).get("status") == "success"
                    for name in COMPARISON_PHASES
                ),
                "every_attempt_has_duration": all(
                    isinstance(normal_phases[name].get("duration_ms"), int)
                    for name in COMPARISON_PHASES
                ),
                "run_timings_match_phase_entries": all(
                    timings.get(f"{name}_ms")
                    == normal_phases[name].get("duration_ms")
                    for name in COMPARISON_PHASES
                ),
                "opaque_diff_timer_removed": "diff_ms" not in timings,
                "metadata_matches_response": (
                    normal_meta.get("comparison_phases") == normal_phases
                ),
                "exact_failure_run_still_succeeds": (
                    failed_exact_result.exit_code == 0
                    and failed_exact.get("status") == "success"
                ),
                "exact_failure_is_attributed": (
                    failed_phases.get("exact_3d_comparison", {}).get("status")
                    == "failed"
                    and "injected exact phase failure" in failed_phases.get(
                        "exact_3d_comparison", {}
                    ).get("message", "")
                ),
                "projection_survives_exact_failure": (
                    failed_phases.get("projection_comparison", {}).get("status")
                    == "success"
                    and failed_exact.get("diff", {}).get(
                        "projection_comparison", {}
                    ).get("method") == "four_view_image_mask"
                ),
                "failed_exact_version_registered": (
                    context_result.exit_code == 0
                    and context.get("version_count") == 3
                    and context.get("current") == "failed_exact"
                ),
                "explicit_diff_reports_skipped_visual_phases": (
                    explicit_result.exit_code == 0
                    and explicit_phases.get("source_loading", {}).get("status")
                    == "success"
                    and explicit_phases.get("exact_3d_comparison", {}).get(
                        "status"
                    ) == "success"
                    and all(
                        explicit_phases.get(name, {}).get("status") == "skipped"
                        for name in (
                            "comparison_rendering",
                            "projection_comparison",
                            "difference_artifact_export",
                            "viewer_generation",
                        )
                    )
                ),
            }
            summary = {
                "status": "success" if all(checks.values()) else "failed",
                "checks": checks,
                "normal_comparison_phases": normal_phases,
                "phases_slowest_first": expensive_first,
                "failed_exact_comparison_phases": failed_phases,
                "explicit_diff_comparison_phases": explicit_phases,
            }
            print(json.dumps(summary, indent=2))
            return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
