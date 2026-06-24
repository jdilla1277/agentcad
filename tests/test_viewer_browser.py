import json
import os

import pytest

from agentcad.cli import cli


GROUPED_PARTS_SCRIPT = """\
import cadquery as cq
base = cq.Workplane("XY").box(20, 10, 2)
rib = cq.Workplane("XY").box(3, 14, 4).translate((0, 0, 3))
pin = cq.Workplane("XY").circle(1).extrude(5).translate((8, 0, 0))
show_object(base, id="base_plate", name="Base Plate", options={
    "part_of": "frame", "group_color": "steelblue"
})
show_object(rib, id="center_rib", name="Center Rib", options={
    "part_of": "frame", "group_color": "steelblue"
})
show_object(pin, id="locator_pin", name="Locator Pin", options={"color": "coral"})
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

    init_result = runner.invoke(cli, ["init", "--name", "browser_smoke"])
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
            "--isolate-group",
            "frame",
            "--ghost-rest",
            "--focus-group",
            "frame",
            "--no-open",
        ],
    )
    assert view_result.exit_code == 0, view_result.output
    viewer = json.loads(view_result.stdout)
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

            assert parts["base_plate"]["visible"] is True
            assert parts["base_plate"]["isolated"] is True
            assert parts["base_plate"]["ghosted"] is False
            assert parts["center_rib"]["visible"] is True
            assert parts["center_rib"]["isolated"] is True
            assert parts["center_rib"]["ghosted"] is False
            assert parts["locator_pin"]["visible"] is True
            assert parts["locator_pin"]["isolated"] is False
            assert parts["locator_pin"]["ghosted"] is True

            assert page.locator('#part-controls [data-group-id="frame"]').evaluate(
                "el => el.classList.contains('selected')"
            )
            assert page.locator('#part-controls [data-part-id="locator_pin"]').evaluate(
                "el => !el.classList.contains('selected')"
            )

            pixels = _canvas_pixel_summary(page)
            assert pixels["width"] > 0
            assert pixels["height"] > 0
            assert pixels["non_background_pixels"] > 1000

            page.click("#btn-parts")
            expect = page.locator("#parts-view")
            assert expect.evaluate("el => getComputedStyle(el).display === 'block'")
            assert page.locator("#parts-heading").inner_text() == "Parts 3 · Groups 1"
            assert page.locator('#parts-groups [data-group-id="frame"]').inner_text() == (
                "frame\nframe · 2 parts"
            )
            assert page.locator('#parts-groups [data-group-id="frame"] .swatch').evaluate(
                "el => el.style.background === 'steelblue'"
            )
            assert page.locator('#parts-list [data-part-id="base_plate"] .part-group-tag').inner_text() == "frame"
            assert page.locator('#parts-list [data-part-id="center_rib"] .part-group-tag').inner_text() == "frame"
            assert page.locator('#parts-list [data-part-id="locator_pin"] .part-group-tag').count() == 0
        finally:
            browser.close()
