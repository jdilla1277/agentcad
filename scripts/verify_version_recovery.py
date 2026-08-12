#!/usr/bin/env python3
"""Development harness for safe interrupted-version reconciliation."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import cadquery as cq
from cadquery import exporters
from click.testing import CliRunner

from agentcad.cli import cli


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agentcad-recovery-") as temp:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=temp):
            project = Path.cwd()
            init = runner.invoke(cli, [
                "init", "--name", "recovery-verification",
                "--runtime", "build123d",
            ])
            if init.exit_code != 0:
                raise RuntimeError(init.output)

            orphan = project / "v1_interrupted_box"
            orphan.mkdir()
            step_path = orphan / "output.step"
            exporters.export(cq.Workplane("XY").box(10, 10, 10), str(step_path))
            script_path = orphan / "script.py"
            script_path.write_text("show_object(Box(10, 10, 10))\n")

            before = json.loads(runner.invoke(cli, ["context"]).stdout)
            candidate = before["recovery"]["candidates"][0]
            recover_result = runner.invoke(cli, [
                "recover", "v1_interrupted_box",
            ])
            recovered = json.loads(recover_result.stdout)
            after = json.loads(runner.invoke(cli, ["context"]).stdout)
            inspected = json.loads(runner.invoke(cli, [
                "inspect", str(step_path), "--no-daemon",
            ]).stdout)
            manifest = json.loads((project / "agentcad.json").read_text())
            meta = json.loads((orphan / "meta.json").read_text())

            checks = {
                "detected_before_recovery": before["recovery"]["status"] == "needed",
                "candidate_recoverable": candidate["recoverable"] is True,
                "explicit_command_provided": (
                    candidate.get("recovery_command")
                    == "agentcad recover v1_interrupted_box"
                ),
                "recover_exit_zero": recover_result.exit_code == 0,
                "recover_success": recovered.get("status") == "success",
                "history_registered_once": len(manifest.get("versions", [])) == 1,
                "current_unchanged": manifest.get("current") is None,
                "step_valid": inspected.get("is_valid") is True,
                "step_preserved": step_path.exists(),
                "script_preserved": script_path.exists(),
                "metadata_restored": meta.get("recovery", {}).get("reconciled") is True,
                "context_clean_after": after["recovery"]["status"] == "clean",
            }
            summary = {
                "status": "success" if all(checks.values()) else "failed",
                "checks": checks,
                "before_recovery": before["recovery"],
                "recover": recovered,
                "after_recovery": after["recovery"],
            }
            print(json.dumps(summary, indent=2))
            return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
