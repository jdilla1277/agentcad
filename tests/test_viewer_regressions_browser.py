import math
import os

import pytest

from agentcad.commands.view import _render_unified


pytestmark = pytest.mark.browser


def _require_playwright():
    if os.environ.get("AGENTCAD_BROWSER_SMOKE") != "1":
        pytest.skip("set AGENTCAD_BROWSER_SMOKE=1 to run browser smoke tests")
    from playwright.sync_api import sync_playwright

    return sync_playwright


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("mode,button", [("parts", "#btn-parts"), ("agent-view", "#btn-agent")])
def test_camera_fit_while_loading_in_hidden_canvas(engine, mode, button, tmp_path):
    sync_playwright = _require_playwright()
    import trimesh

    glb = tmp_path / "model.glb"
    glb.write_bytes(trimesh.creation.box(extents=[20, 10, 5]).export(file_type="glb"))
    html = tmp_path / "viewer.html"
    _render_unified(html, glb, parts=[{"id": "box"}], part_review={"ghost_rest": False})
    # Hold the final load callback until the user has switched out of 3D.
    source = html.read_text().replace(
        "].filter(Boolean)).then(() => {",
        "].filter(Boolean)).then(async () => { await new Promise(resolve => { window.finishModelLoad = resolve; });",
    )
    html.write_text(source)
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(html.as_uri())
            page.wait_for_function("typeof window.finishModelLoad === 'function'", timeout=45_000)
            page.click(button)
            assert not page.locator("#canvas").is_visible()
            page.evaluate("window.finishModelLoad()")
            page.wait_for_function("window.agentcadViewer.debugState().ready")
            state = page.evaluate("window.agentcadViewer.captureState()")
            assert state["mode"] == mode
            assert all(math.isfinite(value) for value in state["position"] + state["target"])
            assert math.dist(state["position"], state["target"]) > 0

            page.click("#btn-single-a")
            page.click("#fit-view-btn")
            before = page.evaluate("window.agentcadViewer.captureState()")
            page.mouse.move(850, 400)
            page.mouse.down()
            page.mouse.move(1000, 450, steps=5)
            page.mouse.up()
            after = page.evaluate("window.agentcadViewer.captureState()")
            assert all(math.isfinite(value) for value in after["position"] + after["target"])
            assert after["position"] != pytest.approx(before["position"])
            assert not errors, errors
        finally:
            browser.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_comparison_pin_updates_do_not_repeat_part_name_matching(engine, tmp_path):
    sync_playwright = _require_playwright()
    import trimesh

    scene = trimesh.Scene()
    parts = []
    for index in range(300):
        part_id = f"part_{index:04d}"
        mesh = trimesh.creation.box()
        mesh.apply_translation([index % 20 * 2, index // 20 * 2, 0])
        scene.add_geometry(mesh, node_name=part_id, geom_name=part_id)
        parts.append({"id": part_id})
    glb = tmp_path / "assembly.glb"
    glb.write_bytes(scene.export(file_type="glb"))
    html = tmp_path / "viewer.html"
    _render_unified(html, glb, glb, parts=parts, parts_model="b", default_mode="side-by-side")
    source = html.read_text().replace(
        "function detectPartId(mesh, parts=PARTS) {",
        "function detectPartId(mesh, parts=PARTS) { window.partNameMatches = (window.partNameMatches || 0) + 1;",
    ).replace(
        "setupReviewComments();\n</script>",
        """setupReviewComments();
reviewComments = Array.from({length: 20}, (_, i) => ({
  id: `C${i + 1}`, text: 'Assembly review', status: 'open',
  target: {part_id: `part_${String(i).padStart(4, '0')}`, scope: 'both', source_model: 'b'},
  anchor: {kind: 'part'},
}));
</script>""",
    )
    html.write_text(source)
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            page.goto(html.as_uri())
            page.wait_for_function("window.agentcadViewer?.debugState().ready", timeout=45_000)
            page.click("#pause-btn")
            initial_matches = page.evaluate("window.partNameMatches")
            assert initial_matches > 0

            def check_pins(expected):
                page.wait_for_function(
                    """expected => [...document.querySelectorAll('.comment-pin')]
                      .filter(pin => getComputedStyle(pin).display !== 'none').length === expected""",
                    arg=expected,
                )
                # No time threshold: even a slow CI runner must do no further
                # name matching as pins are updated on animation frames.
                assert page.evaluate("window.partNameMatches") == initial_matches

            check_pins(40)
            page.click("#btn-overlay")
            check_pins(20)
            page.uncheck("#visible-b")
            check_pins(0)
            page.check("#visible-b")
            check_pins(20)
            page.click("#btn-single-b")
            check_pins(20)
            page.click('#part-controls [data-part-id="part_0000"] [data-action="hide"]')
            check_pins(19)
            # Split view uses separate, visible copies of those same parts.
            page.click("#btn-side")
            check_pins(40)
        finally:
            browser.close()
