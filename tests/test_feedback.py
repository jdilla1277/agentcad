import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from agentcad.cli import cli
from agentcad.commands import feedback as feedback_mod


def test_feedback_subcommand_registered(runner):
    result = runner.invoke(cli, ["feedback", "--help"])
    assert result.exit_code == 0
    assert "feedback" in result.output.lower()


def test_feedback_requires_message(runner, isolated_dir):
    result = runner.invoke(cli, ["feedback"])
    assert result.exit_code != 0


def test_feedback_outputs_bundle_json(runner, isolated_dir):
    # Create a session log first
    (isolated_dir / ".agentcad").mkdir()
    log_file = isolated_dir / ".agentcad" / "session.jsonl"
    entry = json.dumps({
        "timestamp": "2026-03-31T00:00:00+00:00",
        "command": "run",
        "args": {"script": "box.py"},
        "result": {"command": "run", "status": "failed", "error": "syntax error"},
    })
    log_file.write_text(entry + "\n")

    result = runner.invoke(cli, ["feedback", "fillet was confusing"])
    assert result.exit_code == 0

    output = json.loads(result.stdout)
    assert output["command"] == "feedback"
    assert output["status"] == "success"
    assert output["bundle"]["summary"] == "fillet was confusing"
    assert len(output["bundle"]["session_log"]) == 1
    assert output["bundle"]["friction_signals"]["total_commands"] == 1


def test_feedback_writes_bundle_file(runner, isolated_dir):
    result = runner.invoke(cli, ["feedback", "needs better docs"])
    assert result.exit_code == 0

    output = json.loads(result.stdout)
    bundle_path = output["bundle_file"]
    assert Path(bundle_path).exists()

    bundle = json.loads(Path(bundle_path).read_text())
    assert bundle["summary"] == "needs better docs"


def test_feedback_works_with_no_session_log(runner, isolated_dir):
    """Feedback should work even if no commands have been run yet."""
    result = runner.invoke(cli, ["feedback", "just a thought"])
    assert result.exit_code == 0

    output = json.loads(result.stdout)
    assert output["bundle"]["session_log"] == []
    assert output["bundle"]["friction_signals"]["total_commands"] == 0


def test_feedback_bundle_includes_environment(runner, isolated_dir):
    result = runner.invoke(cli, ["feedback", "test env"])
    assert result.exit_code == 0

    output = json.loads(result.stdout)
    env = output["bundle"]["environment"]
    assert "python_version" in env
    assert "agentcad_version" in env
    assert "platform" in env


def test_feedback_max_entries_flag(runner, isolated_dir):
    """--max-entries controls how many session log entries are included."""
    (isolated_dir / ".agentcad").mkdir()
    log_file = isolated_dir / ".agentcad" / "session.jsonl"
    lines = []
    for i in range(20):
        lines.append(json.dumps({
            "timestamp": f"2026-03-31T00:{i:02d}:00+00:00",
            "command": "run",
            "args": {"i": i},
            "result": {"command": "run", "status": "success"},
        }))
    log_file.write_text("\n".join(lines) + "\n")

    result = runner.invoke(cli, ["feedback", "too many", "--max-entries", "5"])
    assert result.exit_code == 0

    output = json.loads(result.stdout)
    assert len(output["bundle"]["session_log"]) == 5


def _write_log_entries(isolated_dir, count):
    (isolated_dir / ".agentcad").mkdir(exist_ok=True)
    log_file = isolated_dir / ".agentcad" / "session.jsonl"
    lines = []
    for i in range(count):
        lines.append(json.dumps({
            "timestamp": f"2026-05-15T00:{i:02d}:00+00:00",
            "command": "run",
            "args": {"i": i},
            "result": {"command": "run", "status": "success"},
        }))
    log_file.write_text("\n".join(lines) + "\n")


def test_feedback_default_max_entries_is_10(runner, isolated_dir):
    """Default --max-entries caps bundled session log at 10 to stay under remote payload limits."""
    _write_log_entries(isolated_dir, 30)

    result = runner.invoke(cli, ["feedback", "size test"])
    assert result.exit_code == 0

    output = json.loads(result.stdout)
    assert len(output["bundle"]["session_log"]) == 10


def test_feedback_status_partial_on_remote_failure(runner, isolated_dir, monkeypatch):
    """When remote upload fails, top-level status is 'partial' so agents notice."""
    monkeypatch.setattr(
        feedback_mod,
        "_send_remote",
        lambda bundle: {"err": "Server returned 500", "discord": None, "neon_row_id": None},
    )

    result = runner.invoke(cli, ["feedback", "remote down"])
    assert result.exit_code == 0

    output = json.loads(result.stdout)
    assert output["status"] == "partial"
    assert "failed" in output["remote"]


def test_feedback_status_success_on_remote_success(runner, isolated_dir):
    """When remote upload succeeds, top-level status remains 'success'."""
    result = runner.invoke(cli, ["feedback", "all good"])
    assert result.exit_code == 0

    output = json.loads(result.stdout)
    assert output["status"] == "success"
    assert output["remote"] == "sent"


def test_feedback_auto_retries_with_fewer_entries_on_413(runner, isolated_dir, monkeypatch):
    """A 413 response triggers automatic retry with halved --max-entries."""
    _write_log_entries(isolated_dir, 40)

    calls = []

    def fake_send(bundle):
        n = len(bundle["session_log"])
        calls.append(n)
        if n > 5:
            return {
                "err": "HTTP Error 413: Request Entity Too Large",
                "discord": None,
                "neon_row_id": None,
            }
        return {"err": None, "discord": "ok", "neon_row_id": 123}

    monkeypatch.setattr(feedback_mod, "_send_remote", fake_send)

    result = runner.invoke(cli, ["feedback", "big bundle", "--max-entries", "40"])
    assert result.exit_code == 0

    output = json.loads(result.stdout)
    # Should have retried, halving each time until <= 5 entries fits
    assert len(calls) > 1
    assert calls[0] == 40
    assert calls[-1] <= 5
    assert output["status"] == "success"
    assert "retried" in output["remote"]
    # Local bundle should reflect what actually got sent
    assert len(output["bundle"]["session_log"]) == calls[-1]


def test_feedback_no_retry_on_non_413_errors(runner, isolated_dir, monkeypatch):
    """Non-413 errors do not trigger the retry loop."""
    _write_log_entries(isolated_dir, 20)

    calls = []

    def fake_send(bundle):
        calls.append(len(bundle["session_log"]))
        return {"err": "Connection refused", "discord": None, "neon_row_id": None}

    monkeypatch.setattr(feedback_mod, "_send_remote", fake_send)

    result = runner.invoke(cli, ["feedback", "network down", "--max-entries", "20"])
    assert result.exit_code == 0

    assert len(calls) == 1  # No retry
    output = json.loads(result.stdout)
    assert output["status"] == "partial"


def test_feedback_413_retry_stops_at_one_entry(runner, isolated_dir, monkeypatch):
    """If 413 persists, retry stops once max_entries reaches 1 (no infinite loop)."""
    _write_log_entries(isolated_dir, 16)
    calls = []

    def always_413(bundle):
        calls.append(len(bundle["session_log"]))
        return {
            "err": "HTTP Error 413: Request Entity Too Large",
            "discord": None,
            "neon_row_id": None,
        }

    monkeypatch.setattr(feedback_mod, "_send_remote", always_413)

    result = runner.invoke(cli, ["feedback", "always too big", "--max-entries", "16"])
    assert result.exit_code == 0

    output = json.loads(result.stdout)
    assert output["status"] == "partial"
    # Halving sequence: 16, 8, 4, 2, 1 — then stop
    assert calls[-1] == 1
    assert len(calls) == 5


def test_feedback_surfaces_discord_status_on_success(runner, isolated_dir, monkeypatch):
    """When API success response includes discord status, CLI surfaces it."""
    def fake_send(bundle):
        return {"err": None, "discord": "ok", "neon_row_id": 42}

    monkeypatch.setattr(feedback_mod, "_send_remote", fake_send)
    result = runner.invoke(cli, ["feedback", "hello"])
    assert result.exit_code == 0

    output = json.loads(result.stdout)
    assert output["status"] == "success"
    assert output["remote"] == "sent"
    assert output["discord"] == "ok"
    assert output["neon_row_id"] == 42


def test_feedback_surfaces_discord_failure_separately(runner, isolated_dir, monkeypatch):
    """API success but Discord failure: status remains success, discord field reports failure."""
    def fake_send(bundle):
        return {
            "err": None,
            "discord": "failed: 400 Bad Request",
            "neon_row_id": 17,
        }

    monkeypatch.setattr(feedback_mod, "_send_remote", fake_send)
    result = runner.invoke(cli, ["feedback", "weird body"])
    assert result.exit_code == 0

    output = json.loads(result.stdout)
    # Neon got the bundle — main job succeeded.
    assert output["status"] == "success"
    # But the user can see the Discord webhook didn't deliver.
    assert "failed: 400" in output["discord"]
    assert output["neon_row_id"] == 17


def test_feedback_local_only_omits_discord_and_neon_fields(runner, isolated_dir):
    """--local-only never hits the remote, so no discord/neon_row_id info exists."""
    result = runner.invoke(cli, ["feedback", "draft", "--local-only"])
    assert result.exit_code == 0

    output = json.loads(result.stdout)
    assert output["status"] == "success"
    assert output["remote"] == "skipped"
    assert output.get("discord") is None
    assert output.get("neon_row_id") is None


def test_feedback_url_honors_env_override(monkeypatch):
    """AGENTCAD_FEEDBACK_URL env var lets tests/self-hosters point at a different endpoint."""
    from agentcad.commands.feedback import _resolve_feedback_url

    monkeypatch.setenv("AGENTCAD_FEEDBACK_URL", "https://preview-xyz.vercel.app/api/feedback")
    assert _resolve_feedback_url() == "https://preview-xyz.vercel.app/api/feedback"

    monkeypatch.delenv("AGENTCAD_FEEDBACK_URL", raising=False)
    assert _resolve_feedback_url() == "https://agentcad.dev/api/feedback"
