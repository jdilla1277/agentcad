import json
import os
import threading
import urllib.parse

import pytest

from agentcad.cli import cli
from agentcad.review_server import ReviewHandler
from agentcad.reviews import create_comment, submit_drafts
from http.server import ThreadingHTTPServer


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
    init_result = runner.invoke(
        cli,
        ["init", "--name", "camera_smoke", "--runtime", "cadquery"],
    )
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


def test_review_viewer_saves_submits_and_reloads_view_comment(runner, isolated_dir):
    sync_playwright = _require_playwright()
    assert runner.invoke(cli, ["init", "--name", "comment_smoke", "--runtime", "cadquery"]).exit_code == 0
    script = isolated_dir / "script.py"
    script.write_text(GROUPED_PARTS_SCRIPT)
    run = runner.invoke(
        cli,
        ["run", "script.py", "--output", "commented", "--no-preview", "--no-view", "--no-daemon"],
    )
    assert run.exit_code == 0, run.output

    token = "browser-test-token"
    server = ThreadingHTTPServer(("127.0.0.1", 0), ReviewHandler)
    server.project_dir = isolated_dir
    server.review_token = token
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    query = urllib.parse.urlencode({"path": "v1_commented/viewer.html", "token": token})
    url = f"http://127.0.0.1:{server.server_port}/viewer?{query}"

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_function("() => window.agentcadViewer?.debugState().ready === true", timeout=45_000)
                page.click("#comment-toggle-btn")
                assert page.locator("#new-comment-btn").is_visible()
                page.click('#part-controls [data-part-id="base_plate"] .name')
                point = page.evaluate("window.agentcadViewer.selectedPartScreenPoint()")
                assert point is not None

                page.click("#new-comment-btn")
                page.mouse.click(point["x"], point["y"])
                page.wait_for_function(
                    "() => window.agentcadViewer.reviewDebugState().pending_anchor?.kind === 'surface'"
                )
                page.fill("#comment-text", "Make the overall silhouette less top-heavy.")
                page.click("#send-comment-btn")
                page.wait_for_function(
                    "() => window.agentcadViewer.reviewDebugState().comments[0]?.status === 'open'"
                )
                agent_reply = runner.invoke(cli, [
                    "review", "reply", "C1", "--message", "Agent is reviewing this.",
                    "--version", "current",
                ])
                assert agent_reply.exit_code == 0, agent_reply.output
                page.wait_for_function(
                    """() => (
                      window.agentcadViewer.reviewDebugState().comments[0]?.replies[0]?.actor
                      === 'agent'
                    )""",
                    timeout=8_000,
                )
                assert "Agent is reviewing this." in page.locator(
                    '.comment-row[data-comment-id="C1"]'
                ).inner_text()

                page.click("#new-comment-btn")
                page.mouse.click(point["x"], point["y"])
                page.wait_for_function(
                    "() => window.agentcadViewer.reviewDebugState().pending_anchor?.kind === 'surface'"
                )
                page.select_option("#comment-part-select", "base_plate")
                page.fill("#comment-text", "Make the base plate easier to mount.")
                page.click("#save-comment-btn")
                page.wait_for_function("() => window.agentcadViewer.reviewDebugState().comments.length === 2")
                second_row = page.locator('.comment-row[data-comment-id="C2"]')
                assert second_row.locator(".draft-note").inner_text() == "Not visible to the agent yet"
                assert second_row.get_by_role("button", name="Send", exact=True).is_visible()
                assert second_row.get_by_role("button", name="Edit", exact=True).is_visible()
                assert second_row.get_by_role("button", name="Delete", exact=True).is_visible()
                second_row.get_by_role("button", name="Edit", exact=True).click()
                page.fill("#comment-text", "Make the base plate easier to mount and align.")
                page.click("#save-comment-btn")
                page.wait_for_function(
                    "() => window.agentcadViewer.reviewDebugState().comments[1]?.text.endsWith('and align.')"
                )

                page.click("#new-comment-btn")
                assert page.locator("body").evaluate("el => el.classList.contains('placing-comment')")
                assert page.locator("#comment-placement-hud").is_visible()
                page.mouse.move(point["x"], point["y"])
                page.wait_for_function(
                    "() => document.querySelector('#comment-target-preview').style.display === 'block'"
                )
                page.mouse.click(point["x"], point["y"])
                page.wait_for_function(
                    "() => window.agentcadViewer.reviewDebugState().pending_anchor?.kind === 'surface'"
                )
                assert page.locator("#comment-part-field").is_visible()
                suggested_part = page.locator("#comment-part-select").input_value()
                assert suggested_part
                assert page.evaluate(
                    "window.agentcadViewer.reviewDebugState().pending_anchor.part_id"
                ) == suggested_part
                page.select_option("#comment-part-select", "locator_pin")
                assert page.evaluate(
                    "window.agentcadViewer.reviewDebugState().pending_anchor.part_id"
                ) == "locator_pin"
                assert page.locator("#comment-target-preview").evaluate(
                    "el => el.classList.contains('locked')"
                )
                page.fill("#comment-text", "Move this surface feature outward.")
                page.click("#save-comment-btn")
                page.wait_for_function("() => window.agentcadViewer.reviewDebugState().comments.length === 3")
                assert not page.locator("#comment-target-preview").is_visible()

                state = page.evaluate("window.agentcadViewer.reviewDebugState()")
                assert [comment["anchor"]["kind"] for comment in state["comments"]] == [
                    "surface", "surface", "surface"
                ]
                assert [comment["status"] for comment in state["comments"]] == [
                    "open", "draft", "draft"
                ]
                assert state["comments"][2]["target"]["part_id"] == "locator_pin"
                x, y, z = state["comments"][2]["anchor"]["point_mm"]
                assert -11 <= x <= 11
                assert -8 <= y <= 8
                assert -2 <= z <= 8
                assert len(state["comments"][2]["anchor"]["part_relative"]) == 3
                page.click("#submit-review-btn")
                page.wait_for_function(
                    "() => window.agentcadViewer.reviewDebugState().comments.every(c => c.status === 'open')"
                )
                first_row = page.locator(".comment-row").first
                first_row.get_by_role("button", name="Reply…").click()
                first_row = page.locator(".comment-row").first
                first_row.locator(".comment-reply-text").fill("One more detail from the human.")
                first_row.get_by_role("button", name="Send reply").click()
                page.wait_for_function(
                    "() => window.agentcadViewer.reviewDebugState().comments[0]?.replies.length === 2"
                )
                first_row = page.locator(".comment-row").first
                first_row.locator(".comment-reply-text").fill("Closing this thread now.")
                first_row.get_by_role("button", name="Resolve").click()
                page.wait_for_function(
                    "() => window.agentcadViewer.reviewDebugState().comments[0]?.status === 'resolved'"
                )
                assert page.evaluate(
                    "window.agentcadViewer.reviewDebugState().comments[0].replies.at(-1).action"
                ) == "resolve"
                first_row = page.locator(".comment-row").first
                first_row.locator(".comment-reply-text").fill("Actually, this still needs work.")
                first_row.get_by_role("button", name="Reopen", exact=True).click()
                page.wait_for_function(
                    "() => window.agentcadViewer.reviewDebugState().comments[0]?.status === 'open'"
                )
                assert page.evaluate(
                    "window.agentcadViewer.reviewDebugState().comments[0].replies.at(-1).action"
                ) == "reopen"

                page.reload(wait_until="domcontentloaded")
                page.wait_for_function(
                    "() => window.agentcadViewer.reviewDebugState().comments.length === 3"
                )
                assert page.locator(".comment-row").count() == 3
                assert page.locator(".comment-pin").count() == 3

                second = runner.invoke(
                    cli,
                    ["run", "script.py", "--output", "revised", "--no-preview", "--no-view", "--no-daemon"],
                )
                assert second.exit_code == 0, second.output
                comparison_query = urllib.parse.urlencode({
                    "path": "v2_revised/viewer.html",
                    "token": token,
                })
                page.goto(
                    f"http://127.0.0.1:{server.server_port}/viewer?{comparison_query}",
                    wait_until="domcontentloaded",
                )
                page.wait_for_function(
                    "() => window.agentcadViewer?.debugState().ready === true",
                    timeout=45_000,
                )
                page.click("#btn-side")
                page.wait_for_timeout(100)
                pin_layout = page.evaluate(
                    """() => Object.fromEntries(
                      [...document.querySelectorAll('.comment-pin')].map(pin => [
                        pin.dataset.commentId,
                        {
                          display: pin.style.display,
                          x: Number.parseFloat(pin.style.left),
                          y: Number.parseFloat(pin.style.top),
                        },
                      ])
                    )"""
                )
                assert pin_layout["C1"]["display"] == "block"
                assert pin_layout["C2"]["display"] == "block"
                assert pin_layout["C3"]["display"] == "block"
                assert pin_layout["C2"]["x"] < 1280 / 2
                assert pin_layout["C3"]["x"] < 1280 / 2
                page.click("#btn-agent")
                assert not page.locator("#comment-toggle-btn").is_visible()
                assert all(
                    pin["display"] == "none"
                    for pin in page.evaluate(
                        """() => [...document.querySelectorAll('.comment-pin')].map(pin => ({
                          display: pin.style.display,
                        }))"""
                    )
                )
                page.click("#btn-parts")
                assert not page.locator("#comment-toggle-btn").is_visible()
                locator_badge = page.locator(
                    '#parts-list [data-part-id="locator_pin"] .part-comment-badge'
                )
                assert locator_badge.is_visible()
                assert "C3" in locator_badge.inner_text()
                page.click("#btn-side")
                page.click("#comment-toggle-btn")
                page.click("#new-comment-btn")
                page.mouse.click(pin_layout["C2"]["x"], pin_layout["C2"]["y"])
                page.wait_for_function(
                    "() => window.agentcadViewer.reviewDebugState().pending_anchor?.kind === 'surface'"
                )
                assert page.evaluate(
                    "window.agentcadViewer.reviewDebugState().pending_anchor.role"
                ) == "a"
                assert page.locator("#comment-scope-field").is_visible()
                assert page.locator('[data-comment-scope="a"]').inner_text() == "Previous"
                assert page.locator('[data-comment-scope="b"]').inner_text() == "Current"
                assert page.locator('[data-comment-scope="both"]').inner_text() == "Both"
                page.click('[data-comment-scope="both"]')
                assert page.evaluate(
                    "window.agentcadViewer.reviewDebugState().pending_anchor.scope"
                ) == "both"
                page.fill("#comment-text", "Keep this feature aligned across both revisions.")
                page.click("#send-comment-btn")
                page.wait_for_function(
                    "() => window.agentcadViewer.reviewDebugState().comments[3]?.status === 'open'"
                )
                both_comment = page.evaluate(
                    "window.agentcadViewer.reviewDebugState().comments[3]"
                )
                assert both_comment["target"]["model"] == "both"
                assert both_comment["target"]["source_model"] == "a"
                assert page.locator('.comment-pin[data-comment-id="C4"]').count() == 2
                assert page.locator('.comment-pin[data-comment-id="C4"]').first.inner_text() == "C4 · Both"

                page.click("#new-comment-btn")
                page.mouse.click(pin_layout["C2"]["x"], pin_layout["C2"]["y"])
                page.wait_for_function(
                    "() => window.agentcadViewer.reviewDebugState().pending_anchor?.kind === 'surface'"
                )
                page.fill("#comment-text", "Temporary draft")
                page.click("#save-comment-btn")
                page.wait_for_function(
                    "() => window.agentcadViewer.reviewDebugState().comments.length === 5"
                )
                page.on("dialog", lambda dialog: dialog.accept())
                page.locator('.comment-row[data-comment-id="C5"]').get_by_role(
                    "button", name="Delete", exact=True
                ).click()
                page.wait_for_function(
                    "() => window.agentcadViewer.reviewDebugState().comments.length === 4"
                )

                third = runner.invoke(
                    cli,
                    ["run", "script.py", "--output", "new-current", "--no-preview", "--no-view", "--no-daemon"],
                )
                assert third.exit_code == 0, third.output
                newest_query = urllib.parse.urlencode({
                    "path": "v3_new-current/viewer.html",
                    "token": token,
                })
                page.goto(
                    f"http://127.0.0.1:{server.server_port}/viewer?{newest_query}",
                    wait_until="domcontentloaded",
                )
                page.wait_for_function(
                    "() => window.agentcadViewer?.debugState().ready === true",
                    timeout=45_000,
                )
                page.click("#btn-side")
                page.wait_for_timeout(100)
                carried_pins = page.evaluate(
                    """() => [...document.querySelectorAll('.comment-pin')]
                      .filter(pin => pin.style.display === 'block')
                      .map(pin => ({
                        id: pin.dataset.commentId,
                        role: pin.dataset.role,
                        x: Number.parseFloat(pin.style.left),
                      }))"""
                )
                for comment_id in ("C1", "C2", "C3"):
                    matches = [pin for pin in carried_pins if pin["id"] == comment_id]
                    assert len(matches) == 1
                    assert matches[0]["role"] == "a"
                    assert matches[0]["x"] < 1280 / 2
                both_pins = [pin for pin in carried_pins if pin["id"] == "C4"]
                assert {pin["role"] for pin in both_pins} == {"a", "b"}
                assert any(pin["x"] < 1280 / 2 for pin in both_pins)
                assert any(pin["x"] > 1280 / 2 for pin in both_pins)

                agent_comment = runner.invoke(cli, [
                    "review", "comment",
                    "--message", "Could the locator pin use a larger lead-in?",
                    "--part", "locator_pin",
                ])
                assert agent_comment.exit_code == 0, agent_comment.output
                agent_payload = json.loads(agent_comment.stdout)["comment"]
                assert agent_payload["author"] == "agent"
                agent_id = agent_payload["id"]
                page.wait_for_function(
                    "() => window.agentcadViewer.reviewDebugState().comments.length === 5",
                    timeout=8_000,
                )
                agent_row = page.locator(f'.comment-row[data-comment-id="{agent_id}"]')
                assert agent_row.locator(".author").inner_text().lower() == "agent"
                assert "Open · Human response needed" in agent_row.inner_text()
                agent_pin = page.locator(f'.comment-pin[data-comment-id="{agent_id}"]')
                assert agent_pin.count() == 1
                assert agent_pin.get_attribute("data-role") == "b"

                page.click("#comment-toggle-btn")
                agent_row.get_by_role("button", name="Reply…").click()
                agent_row.locator(".comment-reply-text").fill("Yes, please add one.")
                agent_row.get_by_role("button", name="Send reply").click()
                page.wait_for_function(
                    "() => window.agentcadViewer.reviewDebugState().comments[4]?.replies[0]?.actor === 'human'"
                )
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    listed = runner.invoke(cli, ["review", "list", "--status", "open"])
    assert listed.exit_code == 0, listed.output
    comments = json.loads(listed.stdout)["comments"]
    assert [comment["anchor"]["kind"] for comment in comments] == [
        "surface", "surface", "surface", "surface", "part"
    ]


def test_carried_surface_comment_maps_by_part_and_missing_part_is_unplaced(runner, isolated_dir):
    sync_playwright = _require_playwright()
    assert runner.invoke(cli, ["init", "--name", "carry_smoke", "--runtime", "cadquery"]).exit_code == 0
    script = isolated_dir / "script.py"
    script.write_text(GROUPED_PARTS_SCRIPT)
    run = runner.invoke(
        cli,
        ["run", "script.py", "--output", "current", "--no-preview", "--no-view", "--no-daemon"],
    )
    assert run.exit_code == 0, run.output

    def carried_payload(part_id, text):
        return {
            "text": text,
            "source_version": 0,
            "source_label": "older",
            "target": {"model": "a", "part_id": part_id},
            "anchor": {
                "kind": "surface",
                "point_mm": [0, 0, 0],
                "normal": [0, 0, 1],
                "part_relative": [0.5, 0.5, 0.5],
            },
            "view": {"mode": "single-a", "position": [30, 30, 30], "target": [0, 0, 0]},
        }

    create_comment(isolated_dir, carried_payload("base_plate", "Still applies to the plate."))
    create_comment(isolated_dir, carried_payload("removed_part", "This part no longer exists."))
    submit_drafts(isolated_dir)

    token = "carry-test-token"
    server = ThreadingHTTPServer(("127.0.0.1", 0), ReviewHandler)
    server.project_dir = isolated_dir
    server.review_token = token
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    query = urllib.parse.urlencode({"path": "v1_current/viewer.html", "token": token})
    url = f"http://127.0.0.1:{server.server_port}/viewer?{query}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_function(
                    "() => window.agentcadViewer?.debugState().ready === true && window.agentcadViewer.reviewDebugState().comments.length === 2",
                    timeout=45_000,
                )
                assert page.locator('.comment-pin[data-comment-id="C1"]').evaluate(
                    "el => getComputedStyle(el).display !== 'none'"
                )
                assert page.locator('.comment-pin[data-comment-id="C2"]').evaluate(
                    "el => getComputedStyle(el).display === 'none'"
                )
                assert page.locator(".comment-row").count() == 2
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
