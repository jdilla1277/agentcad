#!/usr/bin/env python3
"""Development harness for the core-only run fast path."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from click.testing import CliRunner

from agentcad.cli import cli


SCRIPT = """\
from build123d import Box
show_object(Box({size}, 10, 10))
"""

FAST_FLAGS = ["--no-preview", "--no-diff", "--no-view", "--no-daemon"]


def _invoke_json(runner: CliRunner, args: list[str]) -> tuple[object, dict]:
    result = runner.invoke(cli, args)
    if not result.stdout:
        raise RuntimeError(result.exception or "command returned no JSON")
    return result, json.loads(result.stdout)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agentcad-no-diff-") as temp:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=temp):
            project = Path.cwd()
            script = project / "verify.py"
            script.write_text(SCRIPT.format(size=10))

            init, _ = _invoke_json(runner, [
                "init", "--name", "no-diff-verification",
                "--runtime", "build123d",
            ])
            if init.exit_code != 0:
                raise RuntimeError(init.output)

            first, first_payload = _invoke_json(runner, [
                "run", "verify.py", "--output", "first", *FAST_FLAGS,
            ])
            script.write_text(SCRIPT.format(size=12))
            second, second_payload = _invoke_json(runner, [
                "run", "verify.py", "--output", "second", *FAST_FLAGS,
            ])

            version_dir = project / "v2_second"
            files_before_explicit_diff = {
                path.name for path in version_dir.iterdir()
            }
            context_result, context = _invoke_json(runner, ["context"])
            explicit_result, explicit_diff = _invoke_json(runner, [
                "diff", "1", "2", "--no-daemon",
            ])

            artifact_statuses = {
                name: state.get("status")
                for name, state in second_payload.get("artifacts", {}).items()
            }
            timings = second_payload.get("timings", {})
            phases = second_payload.get("completed_phases", [])
            checks = {
                "both_runs_exit_zero": (
                    first.exit_code == 0 and second.exit_code == 0
                ),
                "both_runs_success": (
                    first_payload.get("status") == "success"
                    and second_payload.get("status") == "success"
                ),
                "diff_explicitly_skipped": (
                    artifact_statuses.get("diff") == "skipped"
                ),
                "viewer_assets_explicitly_skipped": (
                    artifact_statuses.get("viewer_glb") == "skipped"
                    and artifact_statuses.get("viewer") == "skipped"
                ),
                "only_core_files_written": files_before_explicit_diff == {
                    "meta.json", "output.step", "script.py",
                },
                "no_comparison_timings": not any(
                    "diff" in name or "viewer" in name for name in timings
                ),
                "no_comparison_phases": not any(
                    name in {"diff", "viewer", "export_viewer_glb"}
                    for name in phases
                ),
                "context_lists_both_versions": (
                    context_result.exit_code == 0
                    and context.get("version_count") == 2
                    and context.get("current") == "second"
                ),
                "explicit_diff_still_works": (
                    explicit_result.exit_code == 0
                    and explicit_diff.get("status") == "success"
                ),
            }
            summary = {
                "status": "success" if all(checks.values()) else "failed",
                "checks": checks,
                "fast_run_artifacts": second_payload.get("artifacts"),
                "fast_run_timings": timings,
                "files_before_explicit_diff": sorted(files_before_explicit_diff),
                "explicit_diff_status": explicit_diff.get("status"),
            }
            print(json.dumps(summary, indent=2))
            return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
