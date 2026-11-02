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
def test_blocked_checks_update_live_status_while_accepted_frame_stays(runner, isolated_dir, monkeypatch):
    if os.environ.get("AGENTCAD_BROWSER_SMOKE") != "1":
        pytest.skip("set AGENTCAD_BROWSER_SMOKE=1")
    from playwright.sync_api import sync_playwright

    assert runner.invoke(cli, ["init", "--name", "blocked-checks"]).exit_code == 0
    script = isolated_dir / "model.py"
    script.write_text(SCRIPT)
    result = runner.invoke(cli, ["run", str(script), "--label", "accepted",
                                "--no-preview", "--no-diff", "--no-daemon"])
    assert result.exit_code == 0, result.output
    accepted = json.loads(result.stdout)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(accepted["project_viewer"]["url"])
            page.wait_for_function("document.querySelector('#status').textContent.includes('Showing v1 · accepted')", timeout=45000)
            element = page.locator("iframe:not(.pending)").element_handle()
            frame = element.content_frame()
            snapshot = page.locator("#snapshot").get_attribute("href")
            cases = [
                ("checks_failed", "blocked by failed checks"),
                ("checks_pending", "awaiting checks"),
                ("checks_unavailable", "blocked by unavailable checks"),
            ]
            for command in ("run", "import"):
                for status, message in cases:
                    check = {"check_id": "length", "kind": "dimension", "axis": "x",
                             "expected_mm": 80 if status == "checks_failed" else 60}
                    if status == "checks_unavailable":
                        check = {"check_id": "vendor", "kind": "external", "validator": "vendor"}
                    requirements = isolated_dir / "checks.json"
                    requirements.write_text(json.dumps({"schema_version": 1, "checks": [check],
                        "actor": "test", "rationale": "Exercise each blocked-attempt status."}))
                    label = f"{command}-{status}"
                    source = str(script) if command == "run" else accepted["outputs"]["step"]
                    args = [command, source, "--label", label, "--checks", str(requirements),
                            "--no-view", "--no-diff", "--no-daemon"]
                    if command == "run":
                        args.append("--no-preview")
                    if status == "checks_pending":
                        args.append("--defer-checks")
                    blocked = runner.invoke(cli, args)
                    assert blocked.exit_code == 1, blocked.output
                    assert json.loads(blocked.stdout)["status"] == status
                    page.wait_for_function("label => document.querySelector('#status').textContent.includes(label)", arg=label)
                    text = page.locator("#status").inner_text()
                    assert message in text
                    assert "Showing v1 · accepted" in text
                    assert "keeping last accepted model" in text
                    assert "Latest build failed" not in text
                    assert "warning" in page.locator("#status").get_attribute("class")
                    assert element.evaluate("element => element.isConnected")
                    assert page.locator("iframe").count() == 1
                    assert page.locator("#snapshot").get_attribute("href") == snapshot
                    assert frame.evaluate("window.agentcadViewer.debugState().ready")
            # A passing but superseded build is an operation conflict. Its
            # retained geometry must not replace the accepted iframe either.
            from agentcad.change_promotion import PromotionStore
            original_commit = PromotionStore.commit
            def supersede(self, *args, **kwargs):
                self.changes.begin(command="run", label="newer", source=SCRIPT.encode(),
                                   source_name="model.py", model={"runtime": "build123d"})
                return original_commit(self, *args, **kwargs)
            monkeypatch.setattr(PromotionStore, "commit", supersede)
            requirements.write_text(json.dumps({"schema_version": 1, "checks": [{
                "check_id": "length", "kind": "dimension", "axis": "x", "expected_mm": 60,
            }], "actor": "test", "rationale": "Exercise superseded valid geometry."}))
            refused = runner.invoke(cli, ["run", str(script), "--label", "selection-conflict",
                                          "--checks", str(requirements), "--no-preview", "--no-view",
                                          "--no-diff", "--no-daemon"])
            assert refused.exit_code == 1, refused.output
            assert json.loads(refused.stdout)["status"] == "not_promoted"
            page.wait_for_function("document.querySelector('#status').textContent.includes('selection-conflict')")
            assert "not accepted; selection or evidence changed" in page.locator("#status").inner_text()
            assert "Showing v1 · accepted" in page.locator("#status").inner_text()
            assert element.evaluate("element => element.isConnected")
            assert page.locator("iframe").count() == 1
            assert page.locator("#snapshot").get_attribute("href") == snapshot
            assert not errors
        finally:
            browser.close()


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
