"""Comments across real live snapshot swaps, in a configured build root."""
import json
import os

import pytest

from agentcad.cli import cli


@pytest.mark.browser
@pytest.mark.timeout(180)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_live_comments_survive_builds_and_preserve_unsent_text(
    engine, runner, isolated_dir,
):
    if os.environ.get("AGENTCAD_BROWSER_SMOKE") != "1":
        pytest.skip("set AGENTCAD_BROWSER_SMOKE=1")
    from playwright.sync_api import sync_playwright

    (isolated_dir / "agentcad.toml").write_text('build_dir = "./build"\n')
    assert runner.invoke(cli, ["init", "--name", "live-comments"]).exit_code == 0
    script = isolated_dir / "part.py"
    script.write_text('show_object(Box(40, 30, 6), id="plate", name="Plate")\n')

    def invoke(*args):
        result = runner.invoke(cli, list(args))
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    def build(number, *, diff=False):
        return invoke("run", "part.py", "--label", f"edit{number}", "--no-preview",
                      "--diff" if diff else "--no-diff", "--no-daemon")

    first = build(1)
    url = first["project_viewer"]["url"]
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(url)

            def showing(number):
                page.wait_for_function(
                    "n => document.querySelector('#status').textContent.includes('Showing v' + n + ' ·')",
                    arg=number, timeout=45000,
                )
                return page.locator("iframe:not(.pending)").element_handle().content_frame()

            frame = showing(1)
            frame.click("#pause-btn")
            frame.click('#part-controls [data-part-id="plate"] .name')
            frame.click("#comment-toggle-btn")
            frame.wait_for_timeout(100)
            point = frame.evaluate("window.agentcadViewer.selectedPartScreenPoint()")
            canvas_box = frame.locator("#canvas").bounding_box()
            panel_box = frame.locator("#comments-panel").bounding_box()
            assert canvas_box["x"] + canvas_box["width"] <= panel_box["x"]
            frame.click("#new-comment-btn")
            # Frame coordinates are local; the live shell's header is outside it.
            frame.locator("#canvas").click(position={"x": point["x"], "y": point["y"]})
            frame.locator("#comment-text").fill("Please soften this edge.")
            second = build(2, diff=True)
            assert second["project_viewer"]["url"] == url
            page.wait_for_function(
                "document.querySelector('#status').textContent.includes('Finish or save your comment')",
                timeout=45000,
            )
            assert "Showing v1" in page.locator("#status").inner_text()
            assert frame.locator("#comment-text").input_value() == "Please soften this edge."
            frame.click("#save-comment-btn")
            frame = showing(2)
            frame.wait_for_function("window.agentcadViewer.reviewDebugState().comments.length === 1")
            row = frame.locator('.comment-row[data-comment-id="C1"]')
            assert row.locator(".draft-note").inner_text() == "Not visible to the agent yet"
            row.get_by_role("button", name="Send", exact=True).click()
            frame.wait_for_function("window.agentcadViewer.reviewDebugState().comments[0].status === 'open'")
            # v1 current was A; v2 current is B. The thread follows Current.
            assert frame.locator('.comment-pin[data-comment-id="C1"]').get_attribute("data-role") == "b"
            row.get_by_role("button", name="Reply…").click()
            row.locator(".comment-reply-text").fill("And leave the mounting face flat.")
            build(3)
            page.wait_for_function(
                "document.querySelector('#status').textContent.includes('Finish or save your comment')",
                timeout=45000,
            )
            assert "Showing v2" in page.locator("#status").inner_text()
            assert row.locator(".comment-reply-text").input_value() == "And leave the mounting face flat."
            row.get_by_role("button", name="Send reply").click()
            frame = showing(3)
            frame.wait_for_function("window.agentcadViewer.reviewDebugState().comments[0]?.replies.length === 1")
            # No comparison was generated for v3, despite successful prior builds.
            previous = runner.invoke(cli, [
                "review", "comment", "--part", "plate", "--scope", "previous", "--message", "Compare",
            ])
            assert previous.exit_code == 1
            agent = invoke("review", "comment", "--part", "plate", "--message", "Is the finish acceptable?")
            assert agent["comment"]["target"]["model"] == "a"
            # Move focus away from an empty reply box so background polling resumes.
            frame.click("#comments-panel h2")
            frame.wait_for_function("window.agentcadViewer.reviewDebugState().comments.length === 2", timeout=8000)
            agent_row = frame.locator('.comment-row[data-comment-id="C2"]')
            assert agent_row.locator(".author").inner_text().lower() == "agent"
            agent_row.get_by_role("button", name="Reply…").click()
            agent_row.locator(".comment-reply-text").fill("Yes, thank you.")
            agent_row.get_by_role("button", name="Resolve", exact=True).click()
            frame.wait_for_function("window.agentcadViewer.reviewDebugState().comments[1].status === 'resolved'")
            stored = invoke("review", "show", "C2")["comment"]
            assert stored["replies"][-1]["actor"] == "human"
            assert stored["replies"][-1]["text"] == "Yes, thank you."
            assert (isolated_dir / "build/.agentcad/reviews/comments.json").exists()
            assert not (isolated_dir / ".agentcad/reviews").exists()
            assert not errors, errors
        finally:
            browser.close()
            # Configured projects own their service separately from the default fixture.
            invoke("viewer", "stop")
