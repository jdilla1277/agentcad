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


# --- read path: list / show / resolve ---------------------------------------
#
# These subcommands call the GET/PATCH side of /api/feedback, guarded by the
# maintainer read key (AGENTCAD_FEEDBACK_READ_KEY). The legacy submit form
# `agentcad feedback "msg"` must keep working — pinned explicitly below.

_READ_KEY = "test-read-key"


@pytest.fixture
def read_key_env(monkeypatch):
    monkeypatch.setenv("AGENTCAD_FEEDBACK_READ_KEY", _READ_KEY)


def _stub_read_api(monkeypatch, data, err=None):
    """Replace _call_read_api, capturing calls. Returns the call list."""
    calls = []

    def fake(method, key, params=None, body=None):
        calls.append({"method": method, "key": key, "params": params, "body": body})
        return (err, None if err else data)

    monkeypatch.setattr(feedback_mod, "_call_read_api", fake)
    return calls


def test_feedback_legacy_message_form_still_works(runner, isolated_dir):
    """`agentcad feedback "msg"` (no subcommand) must keep submitting."""
    result = runner.invoke(cli, ["feedback", "legacy form"])
    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["command"] == "feedback"
    assert output["bundle"]["summary"] == "legacy form"


def test_feedback_legacy_form_with_options_before_message(runner, isolated_dir):
    """Options preceding the message must also fall through to submit."""
    result = runner.invoke(cli, ["feedback", "--local-only", "draft thought"])
    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["remote"] == "skipped"
    assert output["bundle"]["summary"] == "draft thought"


def test_feedback_explicit_submit_subcommand(runner, isolated_dir):
    """`feedback submit` is the explicit path (needed if a message equals a subcommand name)."""
    result = runner.invoke(cli, ["feedback", "submit", "list"])
    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["bundle"]["summary"] == "list"


def test_feedback_help_lists_read_subcommands(runner):
    result = runner.invoke(cli, ["feedback", "--help"])
    assert result.exit_code == 0
    for sub in ("submit", "list", "show", "resolve"):
        assert sub in result.output


def test_feedback_list_requires_read_key(runner, isolated_dir, monkeypatch):
    monkeypatch.delenv("AGENTCAD_FEEDBACK_READ_KEY", raising=False)
    result = runner.invoke(cli, ["feedback", "list"])
    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output["command"] == "feedback list"
    assert output["status"] == "error"
    assert "AGENTCAD_FEEDBACK_READ_KEY" in output["error"]
    assert "suggestion" in output


def test_feedback_list_defaults(runner, isolated_dir, read_key_env, monkeypatch):
    items = [{"id": 3, "created_at": "2026-07-13T10:00:00Z", "status": "new", "summary": "hi"}]
    calls = _stub_read_api(monkeypatch, {"items": items, "count": 1})

    result = runner.invoke(cli, ["feedback", "list"])
    assert result.exit_code == 0

    assert calls == [{
        "method": "GET",
        "key": _READ_KEY,
        "params": {"status": "new", "limit": 20},
        "body": None,
    }]
    output = json.loads(result.stdout)
    assert output["command"] == "feedback list"
    assert output["status"] == "success"
    assert output["items"] == items
    assert output["count"] == 1
    assert output["filter"] == "new"
    # next_actions convention: teach show/resolve, with a more_at pointer
    assert any("feedback show" in a for a in output["next_actions"])
    assert output["more_at"]


def test_feedback_list_status_and_limit_flags(runner, isolated_dir, read_key_env, monkeypatch):
    calls = _stub_read_api(monkeypatch, {"items": [], "count": 0})
    result = runner.invoke(cli, ["feedback", "list", "--status", "all", "--limit", "5"])
    assert result.exit_code == 0
    assert calls[0]["params"] == {"status": "all", "limit": 5}


def test_feedback_show_fetches_full_row(runner, isolated_dir, read_key_env, monkeypatch):
    item = {"id": 7, "status": "new", "payload": {"summary": "hello", "session_log": []}}
    calls = _stub_read_api(monkeypatch, {"item": item})

    result = runner.invoke(cli, ["feedback", "show", "7"])
    assert result.exit_code == 0

    assert calls[0]["method"] == "GET"
    assert calls[0]["params"] == {"id": 7}
    output = json.loads(result.stdout)
    assert output["command"] == "feedback show"
    assert output["status"] == "success"
    assert output["item"] == item


def test_feedback_resolve_patches_status(runner, isolated_dir, read_key_env, monkeypatch):
    calls = _stub_read_api(monkeypatch, {"status": "updated", "id": 7, "feedback_status": "resolved"})

    result = runner.invoke(cli, ["feedback", "resolve", "7"])
    assert result.exit_code == 0

    assert calls == [{
        "method": "PATCH",
        "key": _READ_KEY,
        "params": None,
        "body": {"id": 7, "status": "resolved"},
    }]
    output = json.loads(result.stdout)
    assert output["command"] == "feedback resolve"
    assert output["status"] == "success"
    assert output["id"] == 7
    assert output["feedback_status"] == "resolved"


def test_feedback_resolve_custom_status(runner, isolated_dir, read_key_env, monkeypatch):
    calls = _stub_read_api(monkeypatch, {"status": "updated", "id": 9, "feedback_status": "triaged"})
    result = runner.invoke(cli, ["feedback", "resolve", "9", "--status", "triaged"])
    assert result.exit_code == 0
    assert calls[0]["body"] == {"id": 9, "status": "triaged"}


def test_feedback_read_error_surfaces_with_suggestion(runner, isolated_dir, read_key_env, monkeypatch):
    _stub_read_api(monkeypatch, None, err="HTTP Error 401: Unauthorized.")
    result = runner.invoke(cli, ["feedback", "list"])
    assert result.exit_code == 1

    output = json.loads(result.stdout)
    assert output["status"] == "error"
    assert "401" in output["error"]
    assert "AGENTCAD_FEEDBACK_READ_KEY" in output["suggestion"]
