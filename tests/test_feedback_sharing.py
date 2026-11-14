"""Sharing policy boundaries: real serializers/registered MCP, never production."""
import asyncio
import io
import json
import shutil
from pathlib import Path

import pytest

from agentcad import __version__
from agentcad.cli import cli
from agentcad.commands import feedback as command
from agentcad.feedback_policy import set_sharing_policy, sharing_policy


@pytest.fixture
def uploads(monkeypatch):
    calls = []

    def capture(request, timeout):
        calls.append(json.loads(request.data))
        handle = io.BytesIO(b'{"discord":"ok","neon_row_id":42}')
        handle.status = 200
        return handle

    monkeypatch.setattr(command, "urlopen", capture)
    # Restore the real sender captured at collection, with HTTP intercepted.
    monkeypatch.setattr(command, "_send_remote", REAL_SENDER)
    return calls


from agentcad.commands.feedback import _send_remote as REAL_SENDER


def receipt(runner, args, **kwargs):
    result = runner.invoke(cli, ["feedback", *args], **kwargs)
    assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)


def test_disabled_enabled_revoked_and_no_backlog(runner, isolated_dir, uploads):
    recipient = command._resolve_feedback_url()
    first = receipt(runner, ["private local note"])
    assert not first["submitted"] and first["remote"] == "skipped" and uploads == []
    set_sharing_policy(isolated_dir, recipient, True)
    # Enabling a choice does not replay earlier notes.
    assert uploads == []
    enabled = receipt(runner, ["fillet error was actionable"])
    assert enabled["sharing"]["enabled"] and enabled["submitted"]
    assert uploads == [{"summary": "fillet error was actionable", "agentcad_version": __version__}]
    exact = json.loads(Path(enabled["upload_file"]).read_text())
    assert exact == uploads[0]
    receipt(runner, ["stay local", "--local-only"])
    set_sharing_policy(isolated_dir, recipient, False)
    revoked = receipt(runner, ["after revocation"])
    assert not revoked["submitted"] and len(uploads) == 1


def test_reports_and_long_notes_never_share_automatically(runner, isolated_dir, uploads):
    set_sharing_policy(isolated_dir, command._resolve_feedback_url(), True)
    for text in ["x" * 2001, "a brief file report"]:
        args = [text] if len(text) > 2000 else ["--file", "-"]
        assert not receipt(runner, args, input=text)["submitted"]
    assert uploads == []
    receipt(runner, ["x" * 2000])
    assert len(uploads) == 1


def test_one_off_is_minimal_and_does_not_enable_sharing(runner, isolated_dir, uploads):
    report = "Full report without any inferred attachments. 日本語"
    sent = receipt(runner, ["--file", "-", "--send"], input=report)
    assert sent["submitted"] and not sent["sharing"]["enabled"]
    assert uploads == [{"summary": report, "agentcad_version": __version__}]
    receipt(runner, ["future local note"])
    assert len(uploads) == 1


def test_policy_is_bound_to_root_recipient_and_version(runner, isolated_dir, uploads, monkeypatch):
    recipient = command._resolve_feedback_url()
    set_sharing_policy(isolated_dir, recipient, True)
    other = isolated_dir / "other"
    (other / ".agentcad").mkdir(parents=True)
    shutil.copy(isolated_dir / ".agentcad/feedback-sharing.json", other / ".agentcad/feedback-sharing.json")
    assert not sharing_policy(other, recipient)["enabled"]
    monkeypatch.setenv("AGENTCAD_FEEDBACK_URL", "https://different.invalid/feedback")
    assert not receipt(runner, ["changed recipient"])["submitted"]
    monkeypatch.delenv("AGENTCAD_FEEDBACK_URL")
    policy_file = isolated_dir / ".agentcad/feedback-sharing.json"
    for invalid in ['{', '[]', '{"enabled":true}', '{"enabled":"true"}', '{"policy_version":999,"enabled":true}']:
        policy_file.write_text(invalid)
        assert not receipt(runner, ["invalid policy"])["submitted"]
    assert uploads == []


def test_diagnostics_preview_redact_then_authorize_exact_upload(runner, isolated_dir, uploads):
    (isolated_dir / ".agentcad").mkdir()
    secret = {"command": "run", "args": {"path": "/private/project.py"},
              "result": {"status": "failed", "error": "private design"}}
    (isolated_dir / ".agentcad/session.jsonl").write_text(json.dumps(secret) + "\n")
    set_sharing_policy(isolated_dir, command._resolve_feedback_url(), True)
    preview = receipt(runner, ["A failure", "--diagnostics"])
    assert uploads == [] and not preview["submitted"]
    assert preview["payload"]["session_log"] == [secret]
    assert preview["payload"] == json.loads(Path(preview["bundle_file"]).read_text())
    # Exclude all history and runtime, edit observation text. No fields are re-added.
    redacted = {"summary": "redacted observation", "selected": {"error": "public error"}}
    file = isolated_dir / "reviewed.json"
    file.write_text(json.dumps(redacted))
    denied = runner.invoke(cli, ["feedback", "--send-diagnostics", str(file)])
    assert denied.exit_code != 0 and uploads == []
    sent = receipt(runner, ["--send-diagnostics", str(file), "--send"])
    assert sent["submitted"] and uploads == [redacted]
    assert json.loads(Path(sent["upload_file"]).read_text()) == redacted


@pytest.mark.parametrize("error", ["HTTP Error 413: too large", "DNS lookup failed"])
def test_diagnostic_failure_preserves_exact_payload_without_retry(runner, isolated_dir, monkeypatch, error):
    calls = []
    payload = {"summary": "reviewed", "session_log": [{"redacted": True}]}
    def fail(data):
        calls.append(data)
        assert any(json.loads(p.read_text()) == payload for p in (isolated_dir / ".agentcad/feedback").glob("*.json"))
        return {"err": error}
    monkeypatch.setattr(command, "_send_remote", fail)
    data = receipt(runner, ["--send-diagnostics", "-", "--send"], input=json.dumps(payload))
    assert data["status"] == "partial" and not data["submitted"]
    assert calls == [payload]
    assert json.loads(Path(data["upload_file"]).read_text()) == payload


@pytest.mark.parametrize("args", [
    ["note", "--send", "--local-only"], ["note", "--send", "--diagnostics"],
    ["--send"], ["--template", "--send"],
    ["note", "--send-diagnostics", "-", "--send"],
])
def test_conflicting_inputs_do_no_work(runner, isolated_dir, uploads, args):
    result = runner.invoke(cli, ["feedback", *args], input='{}')
    assert result.exit_code != 0 and uploads == []
    assert not (isolated_dir / ".agentcad/feedback").exists()
    assert not (isolated_dir / ".agentcad/feedback-sharing.json").exists()


def test_registered_mcp_follows_policy_and_selected_build_root(runner, isolated_dir, uploads):
    from agentcad.mcp.server import mcp
    root = isolated_dir / "selected build"
    init = runner.invoke(cli, ["init", "--name", "selected", "--build-dir", str(root), "--no-agent-setup"])
    assert init.exit_code == 0, init.stdout
    def call(**kwargs):
        result = asyncio.run(mcp.call_tool("feedback", {"cwd": str(isolated_dir), "build_dir": str(root), **kwargs}))
        # FastMCP returns text and structured content in current MCP versions.
        content = result[0] if isinstance(result, tuple) else result
        return json.loads(next(item.text for item in content if item.type == "text"))
    guide = call()
    assert guide["submitted"] is False and guide["template"]
    local = call(message="local MCP note")
    assert Path(local["bundle_file"]).is_relative_to(root)
    assert uploads == []
    set_sharing_policy(root, command._resolve_feedback_url(), True)
    call(message="automatic MCP note")
    call(message="explicit local MCP note", local_only=True)
    call(report="detailed report remains local")
    assert uploads == [{"summary": "automatic MCP note", "agentcad_version": __version__}]
    set_sharing_policy(root, command._resolve_feedback_url(), False)
    call(message="revoked MCP note")
    call(report="human-requested report", send=True)
    assert uploads[-1] == {"summary": "human-requested report", "agentcad_version": __version__}
    assert len(uploads) == 2
    preview = call(message="diagnostic note", diagnostics=True)
    assert preview["mode"] == "diagnostic_preview" and len(uploads) == 2
    call(reviewed_payload={"summary": "redacted diagnostic"}, send=True)
    assert uploads[-1] == {"summary": "redacted diagnostic"}
    assert not (isolated_dir / ".agentcad").exists()


def test_mcp_rejects_implicit_policy_setters_and_wrong_boolean_types(isolated_dir, uploads):
    from agentcad.mcp.server import mcp
    for extra in ({"enabled": True}, {"send": "true"}, {"local_only": "false"}):
        with pytest.raises(ValueError):
            asyncio.run(mcp.call_tool("feedback", {"message": "note", "cwd": str(isolated_dir), **extra}))
    assert uploads == [] and not (isolated_dir / ".agentcad").exists()
