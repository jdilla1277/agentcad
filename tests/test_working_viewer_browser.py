"""Real candidate motion between revisions and explicit browser review pins."""

import json
import os
import time

import pytest

from agentcad.cli import cli
from agentcad import project_viewer as live

SOURCE = """length=60
plate=Box(length,30,6)-Pos(-20,0,0)*Cylinder(3,12)-Pos(20,0,0)*Cylinder(3,12)
show_object(fillet(plate.edges().filter_by(Axis.Z),1), id='bracket')
show_object(Pos(0,50,0)*Cylinder(2,12), id='pin')
"""
FLAG = "--experimental-changes"
QUIET = ["--no-preview", "--no-view", "--no-diff", "--no-daemon"]


@pytest.mark.browser
@pytest.mark.timeout(240)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_candidate_follow_pin_diagnostics_and_acceptance(
    engine, runner, isolated_dir, monkeypatch
):
    if os.environ.get("AGENTCAD_BROWSER_SMOKE") != "1":
        pytest.skip("set AGENTCAD_BROWSER_SMOKE=1")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("AGENTCAD_VIEWER_HOME", str(isolated_dir / "runtime"))

    def invoke(args, code=0):
        result = runner.invoke(cli, args)
        assert result.exit_code == code, (result.stdout, result.exception)
        return json.loads(result.stdout)

    invoke(["init", "--name", "working-browser"])
    script = isolated_dir / "model.py"
    script.write_text(SOURCE)

    def build(*extra, code=0):
        return invoke(
            ["run", "model.py", "--label", "bracket", FLAG, *QUIET, *extra], code
        )

    accepted = build()["change"]["revision_id"]
    url = invoke(["viewer", "open", FLAG])["url"]
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.set_default_timeout(60000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(url)

            def displayed(revision):
                page.wait_for_function(
                    "r => document.querySelector('#status').dataset.displayedRevision === r && document.querySelector('iframe:not(.pending)')",
                    arg=revision,
                    timeout=60000,
                )
                return (
                    page.locator("iframe:not(.pending)")
                    .element_handle()
                    .content_frame()
                )

            frame = displayed(accepted)
            frame.click("#pause-btn")
            camera = frame.evaluate("window.agentcadViewer.captureState()")
            requirements = isolated_dir / "checks.json"
            requirements.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "checks": [
                            {
                                "check_id": "length",
                                "kind": "dimension",
                                "axis": "x",
                                "expected_mm": 80,
                            }
                        ],
                    }
                )
            )
            failed = build("--stage", "--checks", "checks.json", code=1)["change"][
                "revision_id"
            ]
            frame = displayed(failed)
            assert "Failed" in page.locator("#status").inner_text()
            assert (
                page.locator("#status").get_attribute("data-accepted-revision")
                == accepted
            )
            transferred = frame.evaluate("window.agentcadViewer.captureState()")
            assert transferred["autoRotate"] is False
            assert transferred["position"] == pytest.approx(camera["position"])
            page.click("#evidence-toggle")
            page.locator("#evidence-content").get_by_text(
                "Expected 80 mm ± 0.1 mm; measured 60 mm.", exact=True
            ).wait_for()
            page.click("#evidence-toggle")
            page.click("#pin")
            assert "revision=" + failed in page.url
            page.click("#evidence-toggle")
            page.wait_for_function(
                "r => JSON.parse(document.querySelector('#evidence-content details pre')?.textContent || 'null')?.baseline?.revision_id === r",
                arg=accepted,
            )
            script.write_text(SOURCE.replace("length=60", "length=80"))
            staged = build("--stage")["change"]["revision_id"]
            invoke(["promote", staged, "--idempotency-key", "human-reviewed", FLAG])
            page.wait_for_function(
                "r => document.querySelector('#status').dataset.acceptedRevision === r",
                arg=staged,
            )
            page.wait_for_function(
                "r => JSON.parse(document.querySelector('#evidence-content details pre')?.textContent || 'null')?.baseline?.revision_id === r",
                arg=staged,
            )
            assert (
                page.locator("#status").get_attribute("data-displayed-revision")
                == failed
            )
            assert "Pinned" in page.locator("#status").inner_text()
            page.reload()
            displayed(failed)
            page.click("#follow")
            displayed(staged)
            assert "Accepted" in page.locator("#status").inner_text()
            script.write_text("value=1/int('0')\n" + SOURCE)
            broken = build("--stage", code=1)["change"]["revision_id"]
            page.wait_for_function(
                "r => document.querySelector('#status').dataset.selectedRevision === r",
                arg=broken,
            )
            assert (
                page.locator("#status").get_attribute("data-displayed-revision")
                == staged
            )
            assert "Showing older geometry" in page.locator("#status").inner_text()
            page.click("#evidence-toggle")
            page.wait_for_function(
                "document.querySelector('#evidence-content').textContent.includes('division')"
            )
            assert frame is not None
            assert not errors
        finally:
            browser.close()
            live.stop_service()


@pytest.mark.browser
@pytest.mark.timeout(60)
def test_evidence_ignores_old_baseline_response_and_refreshes_after_reopen():
    if os.environ.get("AGENTCAD_BROWSER_SMOKE") != "1":
        pytest.skip("set AGENTCAD_BROWSER_SMOKE=1")
    from playwright.sync_api import sync_playwright
    from agentcad.project_viewer_page import PAGE

    selected = "r_" + "1" * 32
    baseline = {"revision_id": "r_" + "2" * 32, "version": 1}
    requests, held = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        def response_for(accepted):
            return {
                "accepted": accepted,
                "checks": {"results": []},
                "diagnostics": {},
                "comparison": (
                    {"baseline": accepted} if accepted["revision_id"] else None
                ),
                "notes": ["Stored comparison"],
            }

        def route(request):
            path = request.request.url.split("?")[0]
            if path.endswith("/state"):
                request.fulfill(
                    json={
                        "experimental_changes": True,
                        "selected": {
                            "revision_id": selected,
                            "status": "Failed",
                            "geometry_available": True,
                        },
                        "accepted": baseline,
                        "latest": None,
                    }
                )
            elif "/evidence/" in path:
                captured = dict(baseline)
                requests.append(captured)
                if len(requests) == 1:
                    held.append((request, captured))
                else:
                    request.fulfill(json=response_for(captured))
            else:
                request.fulfill(body=PAGE, content_type="text/html")

        page.route("http://127.0.0.1:38765/**", route)
        try:
            page.goto("http://127.0.0.1:38765/projects/test/?revision=" + selected)
            page.locator("#evidence-toggle").click()
            # Keep the first baseline's response in flight while promotion
            # changes only the accepted target, not selected ID/status.
            deadline = time.monotonic() + 5
            while not held:
                assert time.monotonic() < deadline
                page.wait_for_timeout(20)
            baseline = {"revision_id": "r_" + "3" * 32, "version": 2}
            page.wait_for_function(
                "r => document.querySelector('#status').dataset.acceptedRevision === r",
                arg=baseline["revision_id"],
            )
            page.wait_for_function(
                "r => JSON.parse(document.querySelector('#evidence-content details pre')?.textContent || 'null')?.baseline?.revision_id === r",
                arg=baseline["revision_id"],
            )
            first, old = held.pop()
            first.fulfill(json=response_for(old))
            page.wait_for_timeout(1200)
            actual = page.locator("#evidence-content details pre").text_content()
            assert (
                json.loads(actual)["baseline"]["revision_id"] == baseline["revision_id"]
            )
            assert len(requests) == 2
            page.locator("#evidence-toggle").click()
            baseline = {"revision_id": None, "version": 3}
            page.wait_for_function(
                "document.querySelector('#status').textContent.includes('Accepted deliverable: v3')"
            )
            page.locator("#evidence-toggle").click()
            page.locator("#evidence-content").get_by_text(
                "Compared with accepted v3", exact=True
            ).wait_for()
            baseline = {"revision_id": None, "version": 4}
            page.locator("#evidence-content").get_by_text(
                "Compared with accepted v4", exact=True
            ).wait_for()
            assert len(requests) == 4
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.timeout(180)
def test_ordinary_edit_restores_standard_page_and_keeps_explicit_pin(
    runner, isolated_dir, monkeypatch
):
    if os.environ.get("AGENTCAD_BROWSER_SMOKE") != "1":
        pytest.skip("set AGENTCAD_BROWSER_SMOKE=1")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("AGENTCAD_VIEWER_HOME", str(isolated_dir / "runtime"))

    def invoke(args):
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, (result.stdout, result.exception)
        return json.loads(result.stdout)

    invoke(["init", "--name", "viewer-opt-in"])
    (isolated_dir / "model.py").write_text(SOURCE)
    original = invoke(["run", "model.py", "--label", "experimental", FLAG, *QUIET])[
        "change"
    ]["revision_id"]
    url = invoke(["viewer", "open", FLAG])["url"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        pinned = browser.new_page()
        try:
            page.goto(url)
            page.wait_for_function(
                "r => document.querySelector('#status').dataset.displayedRevision === r",
                arg=original,
            )
            pinned.goto(url + "?revision=" + original)
            pinned.wait_for_function(
                "r => document.querySelector('#status').dataset.displayedRevision === r",
                arg=original,
            )
            ordinary = invoke(
                ["run", "model.py", "--label", "ordinary", *QUIET, "--view"]
            )
            page.wait_for_function(
                "n => document.querySelector('#status').textContent.includes('Showing v' + n + ' ·')",
                arg=ordinary["version"],
            )
            assert not page.locator("body").evaluate(
                "e => e.classList.contains('experimental')"
            )
            assert page.locator("#pin").is_hidden()
            pinned.wait_for_function(
                "document.querySelector('#status').textContent.includes('Accepted deliverable: v2')"
            )
            assert (
                pinned.locator("#status").get_attribute("data-displayed-revision")
                == original
            )
        finally:
            browser.close()
            live.stop_service()
