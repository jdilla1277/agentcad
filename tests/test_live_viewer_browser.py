"""The actual conversational loop: real CAD builds and a persistent browser."""
import json
import os

import pytest

from agentcad.cli import cli
from agentcad import project_viewer as live


SCRIPT = '''
plate = Box(60, 40, 5) - Cylinder(radius=3, height=10)
rib = Box(4, 30, 12).translate((15, 0, 6))
show_object(plate, id="plate", options={"color":"steelblue"})
show_object(rib, id="rib", options={"color":"coral"})
'''


@pytest.mark.browser
@pytest.mark.timeout(300)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_live_loop_twenty_builds_failure_import_and_restart(engine, runner, isolated_dir, monkeypatch):
    if os.environ.get("AGENTCAD_BROWSER_SMOKE") != "1":
        pytest.skip("set AGENTCAD_BROWSER_SMOKE=1")
    from playwright.sync_api import sync_playwright
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    assert runner.invoke(cli, ["init", "--name", "live-loop"]).exit_code == 0
    script = isolated_dir / "model.py"
    script.write_text(SCRIPT)

    def build(number, extra=()):
        result = runner.invoke(cli, ["run", str(script), "--label", f"edit{number}",
                                    "--no-preview", "--no-diff", "--no-daemon", *extra])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    first = build(1)
    assert "project_viewer" in first, first
    url = first["project_viewer"]["url"]
    errors = []
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        page = browser.new_page(viewport={"width":1280, "height":800})
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(url)

            def showing(number):
                page.wait_for_function("n => document.querySelector('#status').textContent.includes('Showing v' + n + ' ·')", arg=number, timeout=45000)
                frame = page.locator("iframe:not(.pending)").element_handle().content_frame()
                assert frame.evaluate("window.agentcadViewer.debugState().ready")
                return frame

            frame = showing(1)
            frame.click("#pause-btn")
            frame.click("#btn-parts")
            initial = frame.evaluate("window.agentcadViewer.captureState()")
            for number in range(2, 21):
                script.write_text(SCRIPT.replace("radius=3", f"radius={3 + number / 10}"))
                result = build(number)
                assert result["project_viewer"]["url"] == url
                assert result["project_viewer"]["reused"]
                frame = showing(number)
                state = frame.evaluate("window.agentcadViewer.captureState()")
                assert state["mode"] == "parts"
                assert state["autoRotate"] is False
                assert state["position"] == pytest.approx(initial["position"])
                assert state["target"] == pytest.approx(initial["target"])
                assert len(page.context.pages) == 1
            assert opened == [url]

            script.write_text("this is invalid python !")
            failed = runner.invoke(cli, ["run", str(script), "--label", "broken", "--no-daemon"])
            assert failed.exit_code != 0
            page.wait_for_function("document.querySelector('#status').textContent.includes('Latest build failed')")
            assert "Showing v20" in page.locator("#status").inner_text()
            assert frame.evaluate("window.agentcadViewer.debugState().ready")

            # A dry-run failure does not overwrite the live attempt record.
            previous = live.read_json(live.project_record(live.project_token(isolated_dir)))["attempt"]
            runner.invoke(cli, ["run", str(script), "--label", "dry", "--dry-run", "--no-daemon"])
            assert live.read_json(live.project_record(live.project_token(isolated_dir)))["attempt"] == previous

            # Import has no parts panel: the unsupported mode falls back visibly.
            imported = runner.invoke(cli, ["import", str(isolated_dir / "v20_edit20/output.step"),
                                          "--label", "imported", "--no-diff", "--no-daemon"])
            assert imported.exit_code == 0, imported.output
            assert json.loads(imported.stdout)["project_viewer"]["url"] == url
            frame = showing(21)
            assert frame.evaluate("window.agentcadViewer.debugState().mode") == "single-a"
            assert "Previous mode unavailable" in page.locator("#status").inner_text()
            # Actual rendered geometry, not only a ready flag.
            assert frame.locator("#canvas").evaluate("c => c.width > 0 && c.height > 0")

            # Service disconnect leaves the current frame usable and reconnects.
            live.stop_service()
            page.wait_for_function("document.querySelector('#status').textContent.includes('Disconnected')")
            live.ensure_service()
            page.wait_for_function("!document.querySelector('#status').textContent.includes('Disconnected')")
            assert page.url == url
            assert live.open_project(isolated_dir, lambda _: pytest.fail('duplicate after restart'))["reused"]

            # --no-view updates connected pages if it still generates a viewer.
            script.write_text(SCRIPT)
            build(22, ["--no-view", "--preview"])
            frame = showing(22)
            assert frame.evaluate("window.agentcadViewer.debugState().mode") == "single-a"
            # When A becomes the prior model, a current-only selection follows B.
            build(23, ["--diff"])
            frame = showing(23)
            assert frame.evaluate("window.agentcadViewer.debugState().mode") == "single-b"
            frame.click("#btn-overlay")
            overlay = frame.evaluate("window.agentcadViewer.captureState()")
            build(24, ["--diff"])
            frame = showing(24)
            after = frame.evaluate("window.agentcadViewer.captureState()")
            assert after["mode"] == "overlay"
            assert after["position"] == pytest.approx(overlay["position"])
            assert opened == [url]
            assert not errors, errors
        finally:
            browser.close()
