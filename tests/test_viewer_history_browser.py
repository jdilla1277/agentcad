"""History selection, refresh and unsaved-edit protection in the real shell."""
import json
import os
from pathlib import Path

import pytest

from agentcad import project_viewer as live


@pytest.mark.browser
@pytest.mark.skipif(os.environ.get("AGENTCAD_BROWSER_SMOKE") != "1", reason="set AGENTCAD_BROWSER_SMOKE=1")
def test_legacy_saved_preview_opens_directly_without_rebuilding(runner, isolated_dir, monkeypatch):
    from playwright.sync_api import sync_playwright
    from agentcad.cli import cli
    from agentcad.commands import view

    fixture = Path(__file__).parent / "fixtures/viewer/legacy_viewer.html"
    legacy_template = fixture.read_text(encoding="utf-8")
    assert all(message not in legacy_template for message in (
        "agentcad:ready", "agentcad:capture", "agentcad:restore",
    ))
    assert runner.invoke(cli, ["init", "--name", "viewer-upgrade"]).exit_code == 0
    script = isolated_dir / "bracket.py"
    script.write_text('''
base = Box(80, 50, 8) - Cylinder(5, 20)
upright = Box(80, 8, 35).translate((0, -21, 15))
show_object(base + upright, id="bracket", name="Bracket")
show_object(Box(10, 10, 8).translate((60, 0, 0)), id="spacer", name="Spacer")
''')

    def build(label):
        result = runner.invoke(cli, ["run", str(script), "--label", label,
                                     "--no-preview", "--no-diff", "--no-daemon"])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    first = build("old")
    folder = isolated_dir / "v1_old"
    meta = json.loads((folder / "meta.json").read_text())
    # Substitute actual geometry using the unchanged old renderer template.
    with monkeypatch.context() as patch:
        patch.setattr(view, "_HTML_UNIFIED", legacy_template)
        view._render_unified(folder / "viewer.html", folder / "output.glb",
                             label_a="Saved bracket", parts=meta.get("parts"))
    second = build("new")
    url = second["project_viewer"]["url"]
    assert first["project_viewer"]["url"] == url
    saved_paths = [isolated_dir / "agentcad.json", *(folder / name for name in (
        "output.step", "output.glb", "viewer.html", "meta.json",
    ))]
    before = {path: path.read_bytes() for path in saved_paths}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            page.goto(url)
            page.wait_for_function("document.querySelector('#status').textContent.includes('Showing v2')")
            current = page.locator("iframe:not(.pending)").element_handle()
            embedded_requests = []
            page.on("request", lambda request: embedded_requests.append(request.url)
                    if "/artifacts/1/viewer.html?live=1" in request.url else None)
            page.select_option("#history", "1")
            direct = page.get_by_role("link", name="Open v1 snapshot")
            direct.wait_for(state="visible", timeout=8000)
            page.wait_for_function("document.querySelector('#status').textContent.includes('Open the snapshot directly')")
            assert "Showing v2" in page.locator("#status").inner_text()
            assert current.evaluate("element => element.isConnected")
            assert page.locator("iframe.pending").count() == 0
            for _ in range(2):
                page.wait_for_event("response", predicate=lambda response: response.url.startswith(url + "state"))
            assert len(embedded_requests) == 1  # No hidden reload loop for the old viewer.
            with page.expect_popup() as opened:
                direct.click()
            snapshot = opened.value
            snapshot.wait_for_function("window.agentcadViewer?.debugState().ready", timeout=45000)
            assert "/artifacts/1/viewer.html" in snapshot.url
            assert snapshot.locator("#canvas").is_visible()
            assert snapshot.evaluate("window.agentcadViewer.debugState().parts.some(part => part.mesh_count > 0)")
            assert snapshot.evaluate("typeof window.agentcadViewer.captureState") == "undefined"
            snapshot.close()
            # A bookmark of the old selection keeps a working direct link.
            page.reload()
            page.get_by_role("link", name="Open v1 snapshot").wait_for(state="visible")
            page.click("#latest")
            page.wait_for_function("document.querySelector('#status').textContent.includes('Showing v2')")
            assert "version=" not in page.url
            assert all(path.read_bytes() == content for path, content in before.items())
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.skipif(os.environ.get("AGENTCAD_BROWSER_SMOKE") != "1", reason="set AGENTCAD_BROWSER_SMOKE=1")
def test_saved_revision_selection_preserves_context_and_survives_refresh(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("AGENTCAD_VIEWER_HOME", str(tmp_path / "runtime"))
    root = tmp_path / "model"
    root.mkdir()
    entries = []

    def save(number):
        folder = root / f"v{number}_edit"
        folder.mkdir()
        meta = {"version": number, "label": f"edit{number}", "status": "success",
                "created": "2026-11-07T12:00:00Z", "artifacts": {"viewer": {"status": "success"}}}
        (folder / "meta.json").write_text(json.dumps(meta))
        (folder / "output.glb").write_bytes(b"glTF")
        # Exercise the existing ready/capture/restore protocol independently
        # of renderer/CDN availability. Real CAD viewers are covered separately.
        (folder / "viewer.html").write_text(f'''<p>Revision {number}</p><script>
          window.busy = false; window.cameraState = {{position:[8,9,10]}};
          window.agentcadViewer = {{captureState:() => window.cameraState}};
          addEventListener('message', event => {{
            if(event.origin !== location.origin || event.source !== parent) return;
            if(event.data.type === 'agentcad:capture')
              parent.postMessage({{type:'agentcad:state',busy:window.busy,
                busyReason:'Finish or save your comment',state:window.cameraState}},location.origin);
            if(event.data.type === 'agentcad:restore') {{
              if(event.data.state) window.cameraState = event.data.state;
              parent.postMessage({{type:'agentcad:restored'}},location.origin);
            }}
          }});
          parent.postMessage({{type:'agentcad:ready'}},location.origin);
        </script>''')
        entries.append({**meta, "path": folder.name + "/"})
        (root / "agentcad.json").write_text(json.dumps({"versions": entries, "current": f"edit{number}"}))
        return folder

    save(1)
    second = save(2)
    live.publish(root, second, started_ns=2)
    url = live.open_project(root, lambda _: True)["url"]
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.add_init_script("window.viewerEvents=[]; addEventListener('agentcad:viewer-opened', e => viewerEvents.push(e.detail));")
            try:
                page.goto(url)
                page.wait_for_function("document.querySelector('#status').textContent.includes('Showing v2')")
                before = (root / "agentcad.json").read_bytes()
                frame = page.locator("iframe:not(.pending)").element_handle().content_frame()
                frame.evaluate("window.cameraState.position = [15,25,35]; window.busy = true")
                page.select_option("#history", "1")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Finish or save')")
                assert "Showing v2" in page.locator("#status").inner_text()
                frame.evaluate("window.busy = false")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Showing v1')")
                assert "version=1" in page.url
                old = page.locator("iframe:not(.pending)").element_handle().content_frame()
                assert old.evaluate("window.cameraState.position") == [15, 25, 35]
                assert (root / "agentcad.json").read_bytes() == before
                third = save(3)
                live.publish(root, third, started_ns=3)
                page.wait_for_function("document.querySelector('#status').textContent.includes('latest is v3')")
                assert "Showing v1" in page.locator("#status").inner_text()
                page.reload()
                page.wait_for_function("document.querySelector('#status').textContent.includes('Showing v1')")
                restored = page.locator("iframe:not(.pending)").element_handle().content_frame()
                assert restored.evaluate("window.cameraState.position") == [15, 25, 35]
                page.click("#latest")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Showing v3')")
                assert "version=" not in page.url
                assert page.locator("iframe:not(.pending)").count() == 1
                events = page.evaluate("window.viewerEvents")
                assert [event["version"] for event in events] == [1, 3]
                assert events[0]["source"] == "local"
                assert events[0]["selection"] == "history"
                assert str(root) not in json.dumps(events)
                assert url.split("/projects/")[1].strip("/") not in json.dumps(events)
                page.set_viewport_size({"width": 390, "height": 844})
                page.wait_for_function("document.querySelector('iframe').getBoundingClientRect().top >= document.querySelector('header').getBoundingClientRect().bottom")
            finally:
                browser.close()
    finally:
        live.stop_service()
