"""Exercise human sharing controls over HTTP and in the actual browser."""
import json
import os
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from agentcad import project_viewer as live
from agentcad.cli import cli
from agentcad.commands import feedback as command
from agentcad.feedback_policy import sharing_policy
from agentcad.viewer_service import Server
from agentcad.versioning import atomic_write_json


@pytest.fixture
def feedback_viewer(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTCAD_VIEWER_HOME", str(tmp_path / "runtime"))
    root = (tmp_path / "project").resolve()
    root.mkdir()
    token = live.project_token(root)
    atomic_write_json(live.project_record(token), {"root": str(root)})
    calls = []
    monkeypatch.setattr(command, "_send_remote", lambda data: calls.append(data) or {"err": None, "discord": "ok", "neon_row_id": 1})
    server = Server({"port": 0, "secret": "test-admin"})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/projects/{token}/"
    try:
        yield root, token, url, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def request(url, token, route, body=None, headers=None):
    req = Request(url + "feedback/" + route, data=json.dumps(body).encode() if body is not None else None,
                  headers={"X-AgentCAD-Feedback-Token": token, "Content-Type": "application/json", **(headers or {})})
    with urlopen(req, timeout=3) as response:
        return json.load(response)


def test_viewer_policy_requires_project_capability_and_explicit_choice(feedback_viewer):
    root, token, url, calls = feedback_viewer
    recipient = command._resolve_feedback_url()
    assert not request(url, token, "policy")["enabled"]
    for wrong_token, body, headers, status in [
        ("bad", {"enabled": True, "human_authorized": True, "recipient": recipient}, {}, 403),
        (token, {"enabled": True, "recipient": recipient}, {}, 400),
        (token, {"enabled": "true", "human_authorized": True, "recipient": recipient}, {}, 400),
        (token, {"enabled": True, "human_authorized": True, "recipient": "https://changed.invalid"}, {}, 400),
        (token, {"enabled": True, "human_authorized": True, "recipient": recipient}, {"Origin": "https://outside.invalid"}, 403),
    ]:
        with pytest.raises(HTTPError) as error:
            request(url, wrong_token, "policy", body, headers)
        assert error.value.code == status
        assert not sharing_policy(root, recipient)["enabled"]
    for enabled in (True, False):
        saved = request(url, token, "policy", {"enabled": enabled, "human_authorized": True, "recipient": recipient})
        assert saved["enabled"] is enabled
        assert request(url, token, "policy")["enabled"] is enabled
    assert calls == []


def test_viewer_diagnostics_honors_exact_edited_payload_and_authorization(feedback_viewer):
    root, token, url, calls = feedback_viewer
    payload = {"summary": "reviewed", "selected": "redacted"}
    body = {"payload": payload, "recipient": command._resolve_feedback_url()}
    with pytest.raises(HTTPError):
        request(url, token, "diagnostics", body)
    assert calls == []
    sent = request(url, token, "diagnostics", {**body, "human_authorized": True})
    assert sent["submitted"] and calls == [payload]
    assert json.loads(Path(sent["upload_file"]).read_text()) == payload
    assert not sharing_policy(root, command._resolve_feedback_url())["enabled"]


@pytest.fixture
def feedback_receiver():
    uploads = []

    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            uploads.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            body = b'{"neon_row_id": 42, "discord": "ok"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Receiver) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", uploads
        finally:
            server.shutdown()
            thread.join(timeout=3)


def test_reused_subprocess_requires_restart_after_recipient_change(tmp_path, monkeypatch, runner, feedback_receiver):
    # Real service process: changing the caller's environment must not appear
    # to change the destination of a viewer that already captured an older one.
    from agentcad.mcp.server import mcp

    monkeypatch.setenv("AGENTCAD_VIEWER_HOME", str(tmp_path / "runtime"))
    root = (tmp_path / "model").resolve()
    root.mkdir()
    snapshot = root / "v1_part"
    snapshot.mkdir()
    (snapshot / "viewer.html").write_text("<html>Model snapshot</html>")
    (snapshot / "output.glb").write_bytes(b"glTF")
    (snapshot / "meta.json").write_text(json.dumps({
        "status": "success", "version": 1, "label": "part",
        "artifacts": {"viewer": {"status": "success"}},
    }))
    receiver, uploads = feedback_receiver
    endpoint_a = receiver + "/recipient-a"
    endpoint_b = receiver + "/recipient-b"
    monkeypatch.setenv("AGENTCAD_FEEDBACK_URL", endpoint_a)
    monkeypatch.chdir(root)
    try:
        assert live.publish(root, snapshot, started_ns=1)
        opened = live.open_project(root, lambda _: True)
        token, url = live.project_token(root), opened["url"]
        before = live.service_status()
        assert before["feedback_recipient"] == endpoint_a
        choice = {"enabled": True, "human_authorized": True, "recipient": endpoint_a}
        assert request(url, token, "policy", choice)["enabled"]

        monkeypatch.setenv("AGENTCAD_FEEDBACK_URL", endpoint_b)
        with pytest.raises(live.ViewerUnavailable, match="agentcad viewer stop"):
            live.ensure_service()
        cli_open = runner.invoke(cli, ["viewer", "open"])
        assert cli_open.exit_code == 1 and "recipient differs" in cli_open.stdout
        # Neither mismatch detection nor an unshared CLI/MCP observation sends
        # anything to either destination.
        mcp_reply = asyncio.run(mcp.call_tool("feedback", {"message": "local MCP note", "cwd": str(root)}))
        content = mcp_reply[0] if isinstance(mcp_reply, tuple) else mcp_reply
        mcp_receipt = json.loads(next(item.text for item in content if item.type == "text"))
        for result in (
            json.loads(runner.invoke(cli, ["feedback", "local CLI note"]).stdout),
            mcp_receipt,
        ):
            assert result["status"] == "success" and not result["submitted"]
            assert result["sharing"]["recipient"] == endpoint_b
            assert not result["sharing"]["enabled"]
        assert live.service_status()["instance"] == before["instance"]
        with pytest.raises(HTTPError) as error:
            request(url, token, "policy", {**choice, "recipient": endpoint_b})
        assert error.value.code == 400
        assert uploads == []

        # Execute the documented recovery through the public CLI. The bookmark
        # survives, but a choice for A cannot authorize B.
        stopped = runner.invoke(cli, ["viewer", "stop"])
        assert stopped.exit_code == 0, stopped.stdout
        reopened = runner.invoke(cli, ["viewer", "open"])
        assert reopened.exit_code == 0, reopened.stdout
        assert json.loads(reopened.stdout)["url"] == url
        assert live.service_status()["instance"] != before["instance"]
        policy = request(url, token, "policy")
        assert policy["recipient"] == endpoint_b and not policy["enabled"]
        # A stale tab cannot submit a diagnostic to the old recipient after
        # restarting. Validation happens before any network request.
        with pytest.raises(HTTPError) as error:
            request(url, token, "diagnostics", {
                "human_authorized": True, "recipient": endpoint_a,
                "payload": {"summary": "must not upload"},
            })
        assert error.value.code == 400
        assert not list(root.glob(".agentcad/feedback/upload-*.json"))
        renewed = request(url, token, "policy", {**choice, "recipient": endpoint_b})
        assert renewed["enabled"]
        assert sharing_policy(root, endpoint_b)["enabled"]
        assert not sharing_policy(root, endpoint_a)["enabled"]
        # Exercise the real sender inside the subprocess, not a parent-process
        # monkeypatch. The reviewed diagnostic must go only to the new recipient.
        payload = {"summary": "reviewed diagnostic after restart"}
        sent = request(url, token, "diagnostics", {
            "human_authorized": True, "recipient": endpoint_b, "payload": payload,
        })
        assert sent["submitted"] and sent["recipient"] == endpoint_b
        assert uploads == [("/recipient-b", payload)]
    finally:
        live.stop_service()


@pytest.mark.browser
def test_human_enables_revokes_and_redacts_in_browser(feedback_viewer, runner, monkeypatch):
    if os.environ.get("AGENTCAD_BROWSER_SMOKE") != "1":
        pytest.skip("set AGENTCAD_BROWSER_SMOKE=1")
    from playwright.sync_api import sync_playwright, expect
    root, token, url, calls = feedback_viewer
    monkeypatch.chdir(root)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(url)
            expect(page.locator('#feedback-toggle')).to_have_text('Feedback sharing: off')
            page.click('#feedback-toggle')
            expect(page.locator('#feedback-recipient')).to_contain_text(command._resolve_feedback_url())
            expect(page.locator('#feedback-scope')).to_contain_text(str(root))
            page.check('#feedback-enabled')
            page.click('#feedback-save')
            expect(page.locator('#feedback-toggle')).to_have_text('Feedback sharing: on')
            result = runner.invoke(cli, ['feedback', 'brief browser-authorized note'])
            assert result.exit_code == 0, result.stdout
            assert calls[-1]['summary'] == 'brief browser-authorized note'
            page.reload()
            expect(page.locator('#feedback-toggle')).to_have_text('Feedback sharing: on')
            page.click('#feedback-toggle')
            expect(page.locator('#feedback-enabled')).to_be_checked()
            page.uncheck('#feedback-enabled')
            page.click('#feedback-save')
            expect(page.locator('#feedback-toggle')).to_have_text('Feedback sharing: off')
            result = runner.invoke(cli, ['feedback', 'after browser revocation'])
            assert result.exit_code == 0 and len(calls) == 1
            page.locator('#feedback-file').set_input_files({
                'name': 'diagnostic.json', 'mimeType': 'application/json',
                'buffer': json.dumps({'summary': 'diagnostic', 'private_path': '/secret/design'}).encode(),
            })
            expect(page.locator('#feedback-payload')).to_have_value(json.dumps({'summary': 'diagnostic', 'private_path': '/secret/design'}))
            page.check('#feedback-authorize')
            page.fill('#feedback-payload', '{"summary":"redacted diagnostic"}')
            expect(page.locator('#feedback-authorize')).not_to_be_checked()
            expect(page.locator('#feedback-send')).to_be_disabled()
            page.check('#feedback-authorize')
            page.click('#feedback-send')
            expect(page.locator('#feedback-result')).to_contain_text('Diagnostics stored remotely')
            assert calls[-1] == {'summary': 'redacted diagnostic'} and len(calls) == 2
            expect(page.locator('#feedback-authorize')).not_to_be_checked()
            assert errors == []
            screenshot = os.environ.get('AGENTCAD_FEEDBACK_SCREENSHOT')
            if screenshot:
                page.set_viewport_size({"width": 1280, "height": 1200})
                page.locator('#feedback-panel').evaluate('(panel) => { panel.scrollTop = 0; }')
                page.screenshot(path=screenshot)
        finally:
            browser.close()
