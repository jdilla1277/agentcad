#!/usr/bin/env python3
"""End-to-end harness for long daemon-routed run/import/diff commands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from click.testing import CliRunner

from agentcad.cli import cli
from agentcad.daemon import send_request


SCRIPT = """\
from build123d import Box, Cylinder
result = Box({size}, 20, 5) - Cylinder(2, 5)
show_object(result)
"""

SERVER = """\
import sys
import time
import agentcad.daemon as daemon

daemon._PROGRESS_INTERVAL_S = 0.02
original = daemon.DaemonServer._handle_run

def delayed(self, request):
    time.sleep(0.12)
    return original(self, request)

daemon.DaemonServer._handle_run = delayed
daemon.DaemonServer(socket_path=sys.argv[1], pid_path=sys.argv[2]).serve()
"""


def _invoke_json(runner: CliRunner, args: list[str]) -> tuple[object, dict]:
    result = runner.invoke(cli, args)
    if not result.stdout:
        raise RuntimeError(result.exception or "command returned no JSON")
    return result, json.loads(result.stdout)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agentcad-daemon-progress-") as temp:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=temp):
            project = Path.cwd()
            daemon_tag = f"{os.getpid()}-{time.time_ns()}"
            socket_path = f"/tmp/agentcad-progress-{daemon_tag}.sock"
            pid_path = f"/tmp/agentcad-progress-{daemon_tag}.pid"
            script = project / "model.py"
            script.write_text(SCRIPT.format(size=20))
            init, _ = _invoke_json(runner, [
                "init", "--name", "daemon-progress-verification",
                "--runtime", "build123d",
            ])
            baseline, _ = _invoke_json(runner, [
                "run", "model.py", "--output", "baseline",
                "--no-preview", "--no-diff", "--no-view", "--no-daemon",
            ])
            baseline_step = project / "v1_baseline" / "output.step"

            env = dict(os.environ)
            source_root = str(Path(__file__).resolve().parents[1] / "src")
            env["PYTHONPATH"] = os.pathsep.join(filter(None, (
                source_root, env.get("PYTHONPATH"),
            )))
            server = subprocess.Popen(
                [sys.executable, "-c", SERVER, socket_path, pid_path],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                for _ in range(200):
                    if Path(socket_path).exists() and Path(pid_path).exists():
                        break
                    if server.poll() is not None:
                        raise RuntimeError(server.stderr.read())
                    time.sleep(0.01)
                else:
                    raise RuntimeError("verification daemon did not start")

                script.write_text(SCRIPT.format(size=24))
                commands = {
                    "run": [
                        "run", "model.py", "--output", "routed",
                        "--no-preview", "--no-diff", "--no-view",
                    ],
                    "import": [
                        "import", str(baseline_step), "--label", "imported",
                        "--no-diff", "--no-view",
                    ],
                    "diff": ["diff", "1", "2"],
                }
                results = {}
                progress = {}
                for name, argv in commands.items():
                    progress[name] = []
                    results[name] = send_request(
                        {"type": "run", "cwd": str(project), "argv": argv},
                        socket_path=socket_path,
                        response_timeout_s=0.05,
                        progress_callback=progress[name].append,
                    )

                manifest = json.loads((project / "agentcad.json").read_text())
                payloads = {
                    name: json.loads(response["output"])
                    for name, response in results.items()
                }
                checks = {
                    "setup_succeeds": (
                        init.exit_code == 0 and baseline.exit_code == 0
                    ),
                    "all_commands_return_real_success": all(
                        response.get("exit_code") == 0
                        and payloads[name].get("status") == "success"
                        for name, response in results.items()
                    ),
                    "every_command_outlives_idle_window_with_progress": all(
                        frames and frames[-1].get("elapsed_s", 0) > 0.05
                        for frames in progress.values()
                    ),
                    "no_unknown_outcomes": all(
                        payload.get("outcome") != "unknown"
                        and payload.get("error_kind")
                        != "daemon_response_timeout"
                        for payload in payloads.values()
                    ),
                    "versions_recorded_once": (
                        [item["label"] for item in manifest["versions"]]
                        == ["baseline", "routed", "imported"]
                        and manifest["current"] == "imported"
                    ),
                    "routed_step_exists": (
                        project / "v2_routed" / "output.step"
                    ).exists(),
                }
                summary = {
                    "status": "success" if all(checks.values()) else "failed",
                    "checks": checks,
                    "progress_frame_counts": {
                        name: len(frames) for name, frames in progress.items()
                    },
                    "payloads": payloads,
                }
                print(json.dumps(summary, indent=2))
                return 0 if all(checks.values()) else 1
            finally:
                if server.poll() is None:
                    send_request(
                        {"type": "shutdown"},
                        socket_path=socket_path,
                        response_timeout_s=1,
                    )
                    try:
                        server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server.terminate()
                        server.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
