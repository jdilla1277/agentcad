import json

from agentcad.cli import cli


def test_subscribe_subcommand_registered(runner):
    result = runner.invoke(cli, ["subscribe", "--help"])
    assert result.exit_code == 0
    assert "subscribe" in result.output.lower()


def test_subscribe_requires_email(runner, isolated_dir):
    result = runner.invoke(cli, ["subscribe"])
    assert result.exit_code != 0


def test_subscribe_rejects_invalid_email(runner, isolated_dir):
    result = runner.invoke(cli, ["subscribe", "not-an-email"])
    output = json.loads(result.stdout)
    assert output["command"] == "subscribe"
    assert output["status"] == "error"
    assert "email" in output["error"].lower()


def test_subscribe_sends_email_to_remote(runner, isolated_dir, monkeypatch):
    captured = {}

    def fake_send(payload):
        captured["payload"] = payload
        return None, {"status": "pending_confirmation"}

    from agentcad.commands import subscribe as sub_mod
    monkeypatch.setattr(sub_mod, "_send_remote", fake_send)

    result = runner.invoke(cli, ["subscribe", "alice@example.com"])
    assert result.exit_code == 0

    output = json.loads(result.stdout)
    assert output["command"] == "subscribe"
    assert output["status"] == "success"
    assert output["email"] == "alice@example.com"
    assert output["remote"]["status"] == "pending_confirmation"
    assert captured["payload"]["email"] == "alice@example.com"
    assert captured["payload"]["source"] == "cli"


def test_subscribe_includes_agent_context(runner, isolated_dir, monkeypatch):
    """The CLI tags the request with project + tool version so we can tell
    agent-initiated signups from form signups."""
    captured = {}

    def fake_send(payload):
        captured["payload"] = payload
        return None, {"status": "pending_confirmation"}

    from agentcad.commands import subscribe as sub_mod
    monkeypatch.setattr(sub_mod, "_send_remote", fake_send)

    result = runner.invoke(cli, ["subscribe", "bob@example.com"])
    assert result.exit_code == 0

    ctx = captured["payload"].get("agent_context") or {}
    assert "agentcad_version" in ctx
    assert "python_version" in ctx
    assert "platform" in ctx


def test_subscribe_normalises_email(runner, isolated_dir, monkeypatch):
    captured = {}

    def fake_send(payload):
        captured["payload"] = payload
        return None, {"status": "pending_confirmation"}

    from agentcad.commands import subscribe as sub_mod
    monkeypatch.setattr(sub_mod, "_send_remote", fake_send)

    result = runner.invoke(cli, ["subscribe", "  Carol@Example.COM  "])
    assert result.exit_code == 0
    assert captured["payload"]["email"] == "carol@example.com"


def test_subscribe_reports_remote_failure(runner, isolated_dir, monkeypatch):
    from agentcad.commands import subscribe as sub_mod

    monkeypatch.setattr(
        sub_mod, "_send_remote", lambda payload: ("connection refused", None)
    )

    result = runner.invoke(cli, ["subscribe", "dan@example.com"])
    assert result.exit_code != 0
    output = json.loads(result.stdout)
    assert output["command"] == "subscribe"
    assert output["status"] == "error"
    assert "connection refused" in output["error"]
