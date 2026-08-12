"""Session logging for agentcad — captures every command invocation."""

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path


_ERROR_STATUSES = {
    "failed",
    "error",
    "validation_error",
    "invalid_geometry",
}


class SessionLogger:
    def __init__(self, project_dir: Path):
        self._dir = project_dir / ".agentcad"
        self._log_file = self._dir / "session.jsonl"

    def log(self, command: str, args: dict, result: dict) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "args": args,
            "result": result,
        }
        with self._log_file.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def _read_all(self) -> list[dict]:
        if not self._log_file.exists():
            return []
        lines = self._log_file.read_text().strip().splitlines()
        return [json.loads(line) for line in lines]

    def get_recent(self, n: int) -> list[dict]:
        entries = self._read_all()
        return entries[-n:]

    def friction_signals(self) -> dict:
        entries = self._read_all()
        errors = sum(1 for e in entries if e["result"].get("status") in _ERROR_STATUSES)
        successes = sum(1 for e in entries if e["result"].get("status") == "success")

        # Detect retry sequences: consecutive calls to the same command
        retry_sequences = 0
        total_retries = 0
        i = 0
        while i < len(entries):
            cmd = entries[i]["command"]
            j = i + 1
            while j < len(entries) and entries[j]["command"] == cmd:
                j += 1
            run_length = j - i
            if run_length > 1:
                retry_sequences += 1
                total_retries += run_length - 1
            i = j

        return {
            "total_commands": len(entries),
            "errors": errors,
            "successes": successes,
            "retry_sequences": retry_sequences,
            "total_retries": total_retries,
        }

    def get_session_bundle(self, summary: str, max_entries: int = 50) -> dict:
        return {
            "summary": summary,
            "session_log": self.get_recent(max_entries),
            "friction_signals": self.friction_signals(),
            "environment": _environment_info(),
        }


def _environment_info() -> dict:
    try:
        from agentcad import __version__
        version = __version__
    except (ImportError, AttributeError):
        version = "unknown"

    return {
        "python_version": sys.version,
        "agentcad_version": version,
        "platform": platform.platform(),
    }
