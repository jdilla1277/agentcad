import json
import threading
import urllib.error
import urllib.request

import pytest

from agentcad.cli import cli
from agentcad import project_viewer as live
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


def test_cli_default_list_omits_unsent_drafts(runner, isolated_dir):
    assert runner.invoke(cli, ["init", "--name", "drafts"]).exit_code == 0
    draft = create_comment(isolated_dir, _surface_payload())
    result = runner.invoke(cli, ["review", "list"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["comments"] == []
    explicit = runner.invoke(cli, ["review", "list", "--status", "draft"])
    assert json.loads(explicit.stdout)["comments"][0]["id"] == draft["id"]
    submit_drafts(isolated_dir)
    sent = runner.invoke(cli, ["review", "list"])
    assert json.loads(sent.stdout)["comments"][0]["id"] == draft["id"]


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
        "model": "b", "scope": "current", "source_model": "b", "part_id": "support_rib",
    }
    assert current["anchor"] == {
        "kind": "surface", "point_mm": [5.0, 2.0, 1.0],
        "part_relative": [0.25, 0.2, 0.8],
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

    version_dir = isolated_dir / "v1_first"
    version_dir.mkdir()
    (version_dir / "meta.json").write_text(json.dumps({"parts": []}))
    result = runner.invoke(cli, [
        "review", "comment", "--message", "Compare it.", "--part", "rib",
        "--scope", "previous",
    ])
    assert result.exit_code == 1
    assert "no previous successful revision" in result.output


def _registered_project(root):
    root.mkdir(exist_ok=True)
    version = root / "v1_first"
    version.mkdir(exist_ok=True)
    (version / "viewer.html").write_text("<!doctype html><title>served</title>")
    (version / "output.glb").write_bytes(b"glTF")
    (version / "meta.json").write_text(json.dumps({
        "version": 1, "label": "first", "status": "success",
        "artifacts": {"viewer": {"status": "success"}},
    }))
    assert live.publish(root, version, started_ns=1)
    url = live.open_project(root, lambda _: True)["url"]
    token = url.rstrip("/").split("/")[-1]
    return url, token


def _review_request(url, token, route="comments", payload=None, **headers):
    request = urllib.request.Request(
        url + "review/" + route,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"X-AgentCAD-Review-Token": token, "Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.load(response)


def test_live_service_review_threads_and_project_isolation(isolated_dir):
    one = isolated_dir / "one"
    two = isolated_dir / "two"
    url, token = _registered_project(one)
    other_url, other_token = _registered_project(two)
    created = _review_request(url, token, payload={**_surface_payload(), "send": True})
    assert created["comment"]["status"] == "open"
    assert created["comment"]["author"] == "human"
    assert (one / created["comment"]["screenshot"]).read_bytes() == b"abc"
    assert _review_request(other_url, other_token)["comments"] == []
    with pytest.raises(urllib.error.HTTPError) as error:
        _review_request(other_url, token, payload=_surface_payload())
    assert error.value.code == 403
    replied = _review_request(url, token, "comments/C1/reply", {"message": "Details"})
    assert replied["comment"]["replies"][-1]["actor"] == "human"
    for action, status in [("resolve", "resolved"), ("reopen", "open")]:
        result = _review_request(url, token, f"comments/C1/{action}", {"message": action})
        assert result["comment"]["status"] == status
    live.stop_service()
    assert live.open_project(one, lambda _: True)["url"] == url
    assert len(_review_request(url, token)["comments"][0]["replies"]) == 3
    assert live.service_status()["cad_loaded"] is False


@pytest.mark.parametrize("headers", [
    {"Origin": "https://example.com"}, {"Host": "example.com"},
    {"Sec-Fetch-Site": "cross-site"}, {"X-AgentCAD-Review-Token": ""},
])
def test_review_service_rejects_untrusted_writes(isolated_dir, headers):
    url, token = _registered_project(isolated_dir)
    with pytest.raises(urllib.error.HTTPError) as error:
        _review_request(url, token, payload=_surface_payload(), **headers)
    assert error.value.code == 403
    assert list_comments(isolated_dir) == []


@pytest.mark.parametrize("payload", [
    [], {"text": "broken"}, {**_surface_payload(), "target": ["bad"]},
    {**_surface_payload(), "anchor": {"kind": "surface", "point_mm": [1, 2, float("nan")]}},
])
def test_review_service_rejects_invalid_payloads(isolated_dir, payload):
    url, token = _registered_project(isolated_dir)
    with pytest.raises(urllib.error.HTTPError) as error:
        _review_request(url, token, payload=payload)
    assert error.value.code == 400
    assert list_comments(isolated_dir) == []


def test_reviews_follow_build_directory_from_nested_source(runner, isolated_dir, monkeypatch):
    (isolated_dir / "agentcad.toml").write_text('build_dir = "./build"\n')
    assert runner.invoke(cli, ["init", "--name", "configured"]).exit_code == 0
    build = isolated_dir / "build"
    create_comment(build, _surface_payload(), actor="agent")
    nested = isolated_dir / "src"
    nested.mkdir()
    monkeypatch.chdir(nested)
    listed = runner.invoke(cli, ["review", "list", "--status", "open"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout)["count"] == 1
    context = json.loads(runner.invoke(cli, ["context"]).stdout)
    assert context["open_review_comments"] == 1
    assert "--build-dir" in context["review_next_action"]
    replied = runner.invoke(cli, ["review", "reply", "C1", "--message", "Recorded"])
    assert replied.exit_code == 0, replied.output
    assert len(get_comment(build, "C1")["replies"]) == 1
    assert not (nested / ".agentcad/reviews").exists()
    other = isolated_dir / "other"
    assert runner.invoke(cli, ["init", "--build-dir", str(other)]).exit_code == 0
    result = runner.invoke(cli, ["review", "--build-dir", str(other), "list"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["count"] == 0
