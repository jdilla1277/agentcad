import json
import os
import signal
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from agentcad.cli import cli
from agentcad.review_server import ReviewHandler
from agentcad.commands.view import _render_unified
from agentcad.reviews import (
    create_comment,
    delete_draft_comment,
    get_comment,
    list_comments,
    reply_to_comment,
    submit_drafts,
    transition_comment,
    update_draft_comment,
)


def _surface_payload(text="Move this hole"):
    return {
        "text": text,
        "source_version": 1,
        "source_label": "first",
        "target": {"model": "a", "part_id": "plate"},
        "anchor": {
            "kind": "surface",
            "point_mm": [10.0, 2.0, 3.0],
            "normal": [0.0, 0.0, 1.0],
        },
        "view": {"mode": "single-a", "position": [10, 10, 10], "target": [0, 0, 0]},
        "screenshot": "data:image/jpeg;base64,YWJj",
    }


def test_review_store_lifecycle(isolated_dir):
    comment = create_comment(isolated_dir, _surface_payload())
    assert comment["id"] == "C1"
    assert comment["status"] == "draft"
    assert comment["author"] == "human"
    assert comment["anchor"]["point_mm"] == [10.0, 2.0, 3.0]
    assert comment["screenshot"] == ".agentcad/reviews/screenshots/C1.jpg"
    assert (isolated_dir / comment["screenshot"]).read_bytes() == b"abc"

    batch = submit_drafts(isolated_dir)
    assert batch["batch_id"] == "R1"
    assert batch["comments"][0]["status"] == "open"

    addressed = transition_comment(isolated_dir, "C1", "address", version=2)
    assert addressed["status"] == "addressed"
    assert addressed["addressed_in"] == 2

    resolved = transition_comment(isolated_dir, "C1", "resolve")
    assert resolved["status"] == "resolved"
    reopened = transition_comment(isolated_dir, "C1", "reopen")
    assert reopened["status"] == "open"
    assert "addressed_in" not in reopened
    assert get_comment(isolated_dir, "C1")["status"] == "open"


def test_agent_created_comment_starts_open_and_records_author(isolated_dir):
    payload = _surface_payload("Should this mounting hole move?")
    comment = create_comment(isolated_dir, payload, actor="agent")

    assert comment["status"] == "open"
    assert comment["author"] == "agent"
    assert comment["events"] == [{
        "action": "created", "at": comment["created_at"], "actor": "agent",
    }]


def test_draft_can_be_edited_and_deleted_but_sent_comment_cannot(isolated_dir):
    comment = create_comment(isolated_dir, _surface_payload("Original"))
    updated_payload = _surface_payload("Edited before sending")
    updated_payload["target"]["part_id"] = "new_plate"
    updated = update_draft_comment(isolated_dir, comment["id"], updated_payload)
    assert updated["text"] == "Edited before sending"
    assert updated["target"]["part_id"] == "new_plate"
    assert updated["events"][-1]["action"] == "edited"

    submit_drafts(isolated_dir, [comment["id"]])
    with pytest.raises(ValueError, match="Only draft comments can be edited"):
        update_draft_comment(isolated_dir, comment["id"], updated_payload)
    with pytest.raises(ValueError, match="Only draft comments can be deleted"):
        delete_draft_comment(isolated_dir, comment["id"])

    draft = create_comment(isolated_dir, _surface_payload("Throw away"))
    screenshot = isolated_dir / draft["screenshot"]
    assert screenshot.exists()
    deleted = delete_draft_comment(isolated_dir, draft["id"])
    assert deleted["text"] == "Throw away"
    assert get_comment(isolated_dir, draft["id"]) is None
    assert not screenshot.exists()


def test_human_and_agent_can_reply_resolve_and_reopen(isolated_dir):
    comment = create_comment(isolated_dir, _surface_payload("Please revise this"))
    submit_drafts(isolated_dir, [comment["id"]])

    agent_reply = reply_to_comment(
        isolated_dir, comment["id"], "Updated the feature.", actor="agent", version=2
    )
    assert agent_reply["status"] == "open"
    assert agent_reply["replies"][-1] == {
        "actor": "agent", "text": "Updated the feature.",
        "at": agent_reply["replies"][-1]["at"], "version": 2,
    }

    resolved = transition_comment(
        isolated_dir, comment["id"], "resolve", actor="agent", version=2,
        message="Implemented in revision 2.",
    )
    assert resolved["status"] == "resolved"
    assert resolved["replies"][-1]["actor"] == "agent"
    assert resolved["replies"][-1]["action"] == "resolve"

    reopened = transition_comment(
        isolated_dir, comment["id"], "reopen", actor="human",
        message="This still needs adjustment.",
    )
    assert reopened["status"] == "open"
    assert reopened["replies"][-1]["actor"] == "human"
    assert reopened["replies"][-1]["action"] == "reopen"


def test_generated_viewer_embeds_review_context_and_comment_ui(isolated_dir):
    glb = isolated_dir / "model.glb"
    glb.write_bytes(b"glb")
    viewer = isolated_dir / "viewer.html"
    _render_unified(
        viewer,
        glb,
        parts=[{"id": "plate", "name": "Plate"}],
        viewer_context={
            "version": 2,
            "label": "current",
            "models": {"a": {"version": 1}, "b": {"version": 2}},
        },
    )
    html = viewer.read_text()
    assert 'const VIEWER_CONTEXT = {"version": 2' in html
    assert 'id="comment-toggle-btn"' in html
    assert 'id="new-comment-btn"' in html
    assert 'id="comment-placement-hud"' in html
    assert 'id="comment-target-preview"' in html
    assert 'id="comment-part-select"' in html
    assert 'id="comment-scope-options"' in html
    assert 'id="add-surface-comment-btn"' not in html
    assert 'id="add-view-comment-btn"' not in html
    assert "part_relative" in html
    assert "Open · Human response needed" in html
    assert "comment.author === 'agent' ? 'Agent' : 'Human'" in html
    assert "X-AgentCAD-Review-Token" in html


def test_review_store_assigns_unique_ids_across_tabs(isolated_dir):
    created = []

    def create(index):
        created.append(create_comment(isolated_dir, _surface_payload(f"Comment {index}")))

    threads = [threading.Thread(target=create, args=(index,)) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert {comment["id"] for comment in created} == {f"C{index}" for index in range(1, 13)}
    assert len(list_comments(isolated_dir)) == 12


def test_review_cli_and_context_discovery(runner, isolated_dir):
    init = runner.invoke(cli, ["init", "--name", "reviewed"])
    assert init.exit_code == 0, init.output
    create_comment(isolated_dir, _surface_payload("Increase the clearance"))

    before_submit = json.loads(runner.invoke(cli, ["context"]).stdout)
    assert before_submit["open_review_comments"] == 0

    submitted = runner.invoke(cli, ["review", "submit"])
    assert submitted.exit_code == 0, submitted.output
    assert json.loads(submitted.stdout)["batch_id"] == "R1"

    context = json.loads(runner.invoke(cli, ["context"]).stdout)
    assert context["open_review_comments"] == 1
    assert context["pending_review_batch"] == "R1"
    assert context["review_next_action"] == "agentcad review list --status open"

    listed = json.loads(runner.invoke(cli, ["review", "list", "--status", "open"]).stdout)
    assert listed["count"] == 1
    assert listed["comments"][0]["text"] == "Increase the clearance"


def test_review_mark_addressed_requires_saved_version(runner, isolated_dir):
    init = runner.invoke(cli, ["init", "--name", "reviewed"])
    assert init.exit_code == 0
    manifest_path = isolated_dir / "agentcad.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["versions"] = [{
        "version": 2, "label": "fixed", "status": "success", "path": "v2_fixed/"
    }]
    manifest["current"] = "fixed"
    manifest_path.write_text(json.dumps(manifest))
    comment = create_comment(isolated_dir, _surface_payload())
    submit_drafts(isolated_dir)

    result = runner.invoke(cli, ["review", "mark-addressed", comment["id"], "--version", "fixed"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["comment"]["status"] == "addressed"
    assert payload["comment"]["addressed_in"] == 2


def test_agent_cli_can_reply_resolve_and_reopen(runner, isolated_dir):
    init = runner.invoke(cli, ["init", "--name", "reviewed"])
    assert init.exit_code == 0
    manifest_path = isolated_dir / "agentcad.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["versions"] = [{
        "version": 2, "label": "fixed", "status": "success", "path": "v2_fixed/"
    }]
    manifest["current"] = "fixed"
    manifest_path.write_text(json.dumps(manifest))
    comment = create_comment(isolated_dir, _surface_payload())
    submit_drafts(isolated_dir, [comment["id"]])

    replied = runner.invoke(cli, [
        "review", "reply", "C1", "--message", "Updated it.", "--version", "fixed",
    ])
    assert replied.exit_code == 0, replied.output
    reply_payload = json.loads(replied.stdout)["comment"]
    assert reply_payload["replies"][-1]["actor"] == "agent"
    assert reply_payload["replies"][-1]["version"] == 2

    resolved = runner.invoke(cli, [
        "review", "resolve", "C1", "--message", "Complete.", "--version", "fixed",
    ])
    assert resolved.exit_code == 0, resolved.output
    assert json.loads(resolved.stdout)["comment"]["status"] == "resolved"

    reopened = runner.invoke(cli, [
        "review", "reopen", "C1", "--message", "Found another issue.",
    ])
    assert reopened.exit_code == 0, reopened.output
    assert json.loads(reopened.stdout)["comment"]["status"] == "open"


def test_agent_cli_can_initiate_part_comments(runner, isolated_dir):
    assert runner.invoke(cli, ["init", "--name", "reviewed"]).exit_code == 0
    manifest_path = isolated_dir / "agentcad.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["versions"] = [
        {"version": 1, "label": "first", "status": "success", "path": "v1_first/"},
        {"version": 2, "label": "current", "status": "success", "path": "v2_current/"},
    ]
    manifest["current"] = "current"
    manifest_path.write_text(json.dumps(manifest))
    for version, label, x_bounds in [(1, "first", [0, 10]), (2, "current", [0, 20])]:
        version_dir = isolated_dir / f"v{version}_{label}"
        version_dir.mkdir()
        (version_dir / "meta.json").write_text(json.dumps({
            "version": version,
            "label": label,
            "parts": [{
                "id": "support_rib",
                "name": "Support rib",
                "metrics": {"bounding_box": {
                    "x": x_bounds, "y": [0, 10], "z": [0, 5],
                }},
            }],
        }))

    current_result = runner.invoke(cli, [
        "review", "comment", "--message", "Could this rib be thinner?",
        "--part", "support_rib", "--version", "current", "--point-mm", "5,2,1",
    ])
    assert current_result.exit_code == 0, current_result.output
    current = json.loads(current_result.stdout)["comment"]
    assert current["status"] == "open"
    assert current["author"] == "agent"
    assert current["source_version"] == 2
    assert current["target"] == {
        "model": "b", "source_model": "b", "part_id": "support_rib",
    }
    assert current["anchor"] == {
        "kind": "surface", "point_mm": [5.0, 2.0, 1.0],
        "part_relative": [0.25, 0.2, 0.2],
    }

    both_result = runner.invoke(cli, [
        "review", "comment", "--message", "Compare this rib on both revisions.",
        "--part", "support_rib", "--scope", "both",
    ])
    assert both_result.exit_code == 0, both_result.output
    both = json.loads(both_result.stdout)["comment"]
    assert both["target"]["model"] == "both"
    assert both["source_version"] == 2
    assert both["anchor"] == {"kind": "part"}


def test_agent_comment_previous_scope_requires_a_predecessor(runner, isolated_dir):
    assert runner.invoke(cli, ["init", "--name", "reviewed"]).exit_code == 0
    manifest_path = isolated_dir / "agentcad.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["versions"] = [
        {"version": 1, "label": "first", "status": "success", "path": "v1_first/"},
    ]
    manifest["current"] = "first"
    manifest_path.write_text(json.dumps(manifest))

    result = runner.invoke(cli, [
        "review", "comment", "--message", "Compare it.", "--part", "rib",
        "--scope", "previous",
    ])
    assert result.exit_code == 1
    assert "no previous successful revision" in result.output


def test_loopback_review_api_persists_comment(isolated_dir):
    token = "test-token"
    server = ThreadingHTTPServer(("127.0.0.1", 0), ReviewHandler)
    server.project_dir = isolated_dir
    server.review_token = token
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        body = json.dumps(_surface_payload()).encode()
        request = urllib.request.Request(
            base + "/api/comments",
            method="POST",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-AgentCAD-Review-Token": token,
            },
        )
        with urllib.request.urlopen(request) as response:
            created = json.loads(response.read())
        assert created["comment"]["id"] == "C1"
        assert list_comments(isolated_dir)[0]["target"]["part_id"] == "plate"
        assert (isolated_dir / created["comment"]["screenshot"]).read_bytes() == b"abc"

        unauthorized = urllib.request.Request(base + "/api/comments")
        try:
            urllib.request.urlopen(unauthorized)
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("review API accepted a request without its token")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_review_open_starts_loopback_server(runner, isolated_dir, monkeypatch):
    assert runner.invoke(cli, ["init", "--name", "served"]).exit_code == 0
    version_dir = isolated_dir / "v1_first"
    version_dir.mkdir()
    (version_dir / "viewer.html").write_text("<!doctype html><title>served</title>")
    (version_dir / "meta.json").write_text(json.dumps({
        "version": 1,
        "label": "first",
        "status": "success",
        "viewer": "v1_first/viewer.html",
    }))
    manifest_path = isolated_dir / "agentcad.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["versions"] = [{
        "version": 1, "label": "first", "status": "success", "path": "v1_first/"
    }]
    manifest["current"] = "first"
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.delenv("AGENTCAD_REVIEW_SERVER", raising=False)
    monkeypatch.setattr(
        "agentcad.review_server.secrets.token_urlsafe", lambda _length: "-leading-token"
    )

    state_path = isolated_dir / ".agentcad" / "review-server.json"
    state = None
    try:
        result = runner.invoke(cli, ["review", "open", "current", "--no-open"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["url"].startswith("http://127.0.0.1:")
        assert "/viewer?" in payload["url"]
        state = json.loads(state_path.read_text())
        request = urllib.request.Request(
            f"http://127.0.0.1:{state['port']}/api/ping",
            headers={"X-AgentCAD-Review-Token": state["token"]},
        )
        with urllib.request.urlopen(request) as response:
            assert json.loads(response.read())["status"] == "ok"
    finally:
        if state:
            os.kill(int(state["pid"]), signal.SIGTERM)
