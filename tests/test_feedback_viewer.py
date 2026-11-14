"""Exercise human sharing controls over HTTP and in the actual browser."""
import json
import os
import threading
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
