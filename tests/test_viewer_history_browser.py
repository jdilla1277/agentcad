"""History selection, refresh and unsaved-edit protection in the real shell."""
import json
import os

import pytest

from agentcad import project_viewer as live


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
