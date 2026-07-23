import json
import os

import pytest

from agentcad.cli import cli


GROUPED_PARTS_SCRIPT = """\
import cadquery as cq
base = cq.Workplane("XY").box(20, 10, 2)
rib = cq.Workplane("XY").box(3, 14, 4).translate((0, 0, 3))
pin = cq.Workplane("XY").circle(1).extrude(5).translate((8, 0, 0))
cover = cq.Workplane("XY").box(12, 8, 1).translate((0, 0, 6))
show_object(base, id="base_plate", name="Base Plate", options={
    "part_of": "frame", "group_color": "steelblue"
})
show_object(rib, id="center_rib", name="Center Rib", options={
    "part_of": "frame", "group_color": "steelblue"
})
show_object(pin, id="locator_pin", name="Locator Pin", options={"color": "coral"})
show_object(cover, id="cover_panel", name="Cover Panel", options={
    "part_of": "cover", "group_color": "forestgreen"
})
"""


pytestmark = pytest.mark.browser


def _require_playwright():
    if os.environ.get("AGENTCAD_BROWSER_SMOKE") != "1":
        pytest.skip("set AGENTCAD_BROWSER_SMOKE=1 to run browser smoke tests")
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        pytest.fail(f"Playwright is required for browser smoke tests: {exc}")
    return sync_playwright


def _canvas_pixel_summary(page):
    return page.evaluate(
        """() => {
          const canvas = document.getElementById('canvas');
          const probe = document.createElement('canvas');
          probe.width = canvas.width;
          probe.height = canvas.height;
          const ctx = probe.getContext('2d', { willReadFrequently: true });
          ctx.drawImage(canvas, 0, 0);
          const data = ctx.getImageData(0, 0, probe.width, probe.height).data;
          let nonBackground = 0;
          for (let i = 0; i < data.length; i += 4) {
            const r = data[i], g = data[i + 1], b = data[i + 2], a = data[i + 3];
            if (a > 0 && (Math.abs(r - 239) > 4 || Math.abs(g - 239) > 4 || Math.abs(b - 239) > 4)) {
              nonBackground += 1;
            }
          }
          return {
            width: probe.width,
            height: probe.height,
            non_background_pixels: nonBackground,
          };
        }"""
    )


def test_group_review_viewer_isolates_group_with_ghost_rest(runner, isolated_dir):
    sync_playwright = _require_playwright()

    init_result = runner.invoke(
        cli,
        ["init", "--name", "browser_smoke", "--runtime", "cadquery"],
    )
    assert init_result.exit_code == 0, init_result.output
    script = isolated_dir / "script.py"
    script.write_text(GROUPED_PARTS_SCRIPT)
    run_result = runner.invoke(
        cli,
        ["run", "script.py", "--output", "grouped", "--no-preview", "--no-daemon"],
    )
    assert run_result.exit_code == 0, run_result.output

    view_result = runner.invoke(
        cli,
        [
            "parts",
            "view",
            "grouped",
            "--label",
            "Frame check",
            "--note",
            "Inspect the frame before approving.",
            "--isolate-group",
            "frame",
            "--ghost-rest",
            "--focus-group",
            "frame",
            "--hide-group",
            "cover",
            "--no-open",
        ],
    )
    assert view_result.exit_code == 0, view_result.output
    viewer = json.loads(view_result.stdout)
    assert viewer["part_review"]["review_label"] == "Frame check"
    assert viewer["part_review"]["note"] == "Inspect the frame before approving."
    assert viewer["part_review"]["isolated_groups"] == ["frame"]
    assert viewer["part_review"]["isolated"] == ["base_plate", "center_rib"]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            page.goto(viewer["url"], wait_until="domcontentloaded")
            page.wait_for_function(
                """() => (
                  window.agentcadViewer
                  && window.agentcadViewer.debugState
                  && window.agentcadViewer.debugState().ready === true
                  && window.agentcadViewer.debugState().parts.every(p => p.mesh_count > 0)
                )""",
                timeout=45_000,
            )
            page.wait_for_timeout(250)

            state = page.evaluate("window.agentcadViewer.debugState()")
            parts = {part["id"]: part for part in state["parts"]}
            groups = {group["id"]: group for group in state["groups"]}

            assert state["mode"] == "single-a"
            assert state["ghost_rest"] is True
            assert groups["frame"]["selected"] is True
            assert groups["frame"]["isolated"] is True
            assert groups["cover"]["hidden"] is True
            assert page.locator("#part-handoff").evaluate(
                "el => getComputedStyle(el).display === 'block'"
            )
            assert page.locator("#part-handoff-title").inner_text() == "Frame check"
            assert page.locator("#part-handoff-note").inner_text() == (
                "Inspect the frame before approving."
            )

            assert parts["base_plate"]["visible"] is True
            assert parts["base_plate"]["isolated"] is True
            assert parts["base_plate"]["ghosted"] is False
            assert parts["center_rib"]["visible"] is True
            assert parts["center_rib"]["isolated"] is True
            assert parts["center_rib"]["ghosted"] is False
            assert parts["locator_pin"]["visible"] is True
            assert parts["locator_pin"]["isolated"] is False
            assert parts["locator_pin"]["ghosted"] is True
            assert parts["cover_panel"]["visible"] is False
            assert parts["cover_panel"]["hidden"] is True

            assert page.locator('#part-controls [data-group-id="frame"]').evaluate(
                "el => el.classList.contains('selected')"
            )
            assert page.locator('#part-controls [data-part-id="locator_pin"]').evaluate(
                "el => !el.classList.contains('selected')"
            )
            assert page.locator('#part-controls [data-group-id="cover"]').evaluate(
                "el => el.classList.contains('hidden')"
            )

            pixels = _canvas_pixel_summary(page)
            assert pixels["width"] > 0
            assert pixels["height"] > 0
            assert pixels["non_background_pixels"] > 1000

            page.click("#btn-parts")
            expect = page.locator("#parts-view")
            assert expect.evaluate("el => getComputedStyle(el).display === 'block'")
            assert page.locator("#parts-heading").inner_text() == "Parts 4 · Groups 2"
            assert page.locator('#parts-groups [data-group-id="frame"]').inner_text() == (
                "frame\nframe · 2 parts"
            )
            assert page.locator('#parts-groups [data-group-id="frame"] .swatch').evaluate(
                "el => el.style.background === 'steelblue'"
            )
            assert page.locator('#parts-list [data-part-id="base_plate"] .part-group-tag').inner_text() == "frame"
            assert page.locator('#parts-list [data-part-id="center_rib"] .part-group-tag').inner_text() == "frame"
            assert page.locator('#parts-list [data-part-id="locator_pin"] .part-group-tag').count() == 0
            assert page.locator('#parts-list [data-part-id="cover_panel"] .part-group-tag').inner_text() == "cover"

            assert page.locator("#btn-agent").is_enabled()
            page.click("#btn-agent")
            assert page.locator("#agent-view").evaluate(
                "el => getComputedStyle(el).display === 'block'"
            )
            assert page.locator("#agent-handoff-heading").inner_text() == "Frame check"
            assert page.locator("#agent-handoff-note").inner_text() == (
                "Inspect the frame before approving."
            )
            assert "frame (2 parts)" in page.locator("#agent-handoff-details").inner_text()
            assert "cover (1 part)" in page.locator("#agent-handoff-details").inner_text()
            assert "Base Plate (base_plate)" in page.locator("#agent-handoff-details").inner_text()
            assert "Center Rib (center_rib)" in page.locator("#agent-handoff-details").inner_text()
            assert "Cover Panel (cover_panel)" in page.locator("#agent-handoff-details").inner_text()
            assert "Ghost rest\nOn" in page.locator("#agent-handoff-details").inner_text()
            assert "Lifecycle\nTemporary, not saved" in page.locator("#agent-handoff-details").inner_text()
            assert page.locator("#panel-agent-images-empty").evaluate(
                "el => getComputedStyle(el).display === 'block'"
            )
            assert "The agent did not ask for a preview on this run." in page.locator(
                "#panel-agent-images-empty"
            ).inner_text()
        finally:
            browser.close()


def test_previous_current_modes_preserve_camera_orientation(runner, isolated_dir):
    sync_playwright = _require_playwright()
    init_result = runner.invoke(cli, ["init", "--name", "camera_smoke"])
    assert init_result.exit_code == 0, init_result.output
    script = isolated_dir / "script.py"
    script.write_text(GROUPED_PARTS_SCRIPT)
    first = runner.invoke(
        cli,
        ["run", "script.py", "--output", "first", "--no-preview", "--no-view", "--no-daemon"],
    )
    assert first.exit_code == 0, first.output
    second = runner.invoke(
        cli,
        ["run", "script.py", "--output", "second", "--no-preview", "--no-view", "--no-daemon"],
    )
    assert second.exit_code == 0, second.output
    viewer_url = (isolated_dir / "v2_second" / "viewer.html").as_uri()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            page.goto(viewer_url, wait_until="domcontentloaded")
            page.wait_for_function(
                "() => window.agentcadViewer?.debugState().ready === true",
                timeout=45_000,
            )
            page.click("#pause-btn")
            before = page.evaluate("window.agentcadViewer.debugState().camera")
            for button in ("#btn-single-a", "#btn-single-b", "#btn-overlay", "#btn-side"):
                page.click(button)
                after = page.evaluate("window.agentcadViewer.debugState().camera")
                assert after == before
        finally:
            browser.close()
