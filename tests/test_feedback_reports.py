"""Guided feedback keeps the report intact without losing quick notes."""

import io
import json
import shlex
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentcad.commands import feedback as feedback_mod
from agentcad.cli import cli


REPORT = """## Summary
The lid's mounting holes are missing.

## What I was trying to do
Build a case for a 28 × 51 mm board, then change the lid.

## Feedback
The STEP was valid, but the lid had no holes. Привет, 日本語.
Literal shell text: $(touch should-not-exist) `whoami` "quotes".

## Expected behavior
Four matching mounting holes.

## Impact or workaround
I measured the lid and rebuilt it manually.
"""


def payload(result):
    return json.loads(result.stdout)


@pytest.fixture
def legacy_cp1252(monkeypatch):
    # Control actual pathlib I/O, including under Python's UTF-8 mode.
    monkeypatch.setattr(
        io,
        "text_encoding",
        lambda encoding, stacklevel=2: (
            "cp1252" if encoding in (None, "locale") else encoding
        ),
    )


def test_bare_feedback_guides_without_sending_or_creating_state(
    runner, isolated_dir, monkeypatch
):
    monkeypatch.setattr(
        feedback_mod, "_send_remote", lambda _: pytest.fail("sent template")
    )
    result = runner.invoke(cli, ["feedback"])
    assert result.exit_code == 0, result.stdout
    data = payload(result)
    assert data["mode"] == "template" and data["submitted"] is False
    for heading in (
        "Summary",
        "What I was trying to do",
        "Feedback",
        "Expected behavior",
        "Reproduction and evidence",
        "Impact or workaround",
        "What worked well",
        "Ideas",
    ):
        assert f"## {heading}" in data["template"]
    assert data["next_actions"] and data["more_at"] == "agentcad docs feedback"
    assert not (isolated_dir / ".agentcad").exists()


def test_explicit_template_is_redirection_safe(runner, isolated_dir, monkeypatch):
    from agentcad.cli import cli

    monkeypatch.setattr(
        feedback_mod, "_send_remote", lambda _: pytest.fail("sent template")
    )
    result = runner.invoke(cli, ["feedback", "--template"])
    assert result.exit_code == 0
    assert result.stdout.startswith("## Summary\n")
    assert "agentcad feedback --file" not in result.stdout
    assert "agentcad feedback --file" in result.stderr
    assert not (isolated_dir / ".agentcad").exists()


def test_template_file_is_utf8_and_does_not_overwrite(
    runner, isolated_dir, monkeypatch, legacy_cp1252
):
    from agentcad.cli import cli

    monkeypatch.setattr(
        feedback_mod, "FEEDBACK_TEMPLATE", "## Summary\nПривет × 日本語\n"
    )
    result = runner.invoke(cli, ["feedback", "--template", "--output", "my report.md"])
    assert result.exit_code == 0, result.stdout
    data = payload(result)
    report = Path(data["template_file"])
    assert report.read_bytes() == "## Summary\nПривет × 日本語\n".encode("utf-8")
    assert data["submitted"] is False
    assert "my report.md" in data["next_actions"][0]
    report.write_bytes(b"existing user notes")
    refused = runner.invoke(cli, ["feedback", "--template", "--output", "my report.md"])
    assert refused.exit_code != 0
    assert payload(refused)["status"] == "error"
    assert report.read_bytes() == b"existing user notes"
    assert not (isolated_dir / ".agentcad").exists()


@pytest.mark.parametrize("use_stdin", [False, True])
def test_report_preserves_full_text_and_context_with_compact_receipt(
    runner, isolated_dir, monkeypatch, use_stdin, legacy_cp1252
):
    from agentcad.cli import cli

    runner.invoke(cli, ["init", "--name", "feedback-project", "--no-agent-setup"])
    sent = []
    monkeypatch.setattr(
        feedback_mod,
        "_send_remote",
        lambda bundle: (
            sent.append(bundle) or {"err": None, "discord": "ok", "neon_row_id": 42}
        ),
    )
    report = isolated_dir / "feedback with spaces.md"
    report.write_text(REPORT, encoding="utf-8")
    result = runner.invoke(
        cli,
        ["feedback", "--file", "-" if use_stdin else str(report)],
        input=REPORT if use_stdin else None,
    )
    assert result.exit_code == 0, result.stdout
    data = payload(result)
    assert data["status"] == "success" and data["neon_row_id"] == 42
    assert "bundle" not in data and "Привет" not in result.stdout
    saved = json.loads(Path(data["bundle_file"]).read_text(encoding="utf-8"))
    assert saved == sent[0]
    assert saved["report"] == REPORT
    assert saved["summary"] == "The lid's mounting holes are missing."
    assert saved["report_format"] == "markdown"
    assert saved["session_log"] and saved["environment"]["agentcad_version"]
    assert not (isolated_dir / "should-not-exist").exists()


@pytest.mark.parametrize(
    "text",
    [
        "tiny partial thought",
        "## Feedback\nOnly one observation.",
        "\n    indented diagnostic\n\n",
    ],
)
def test_partial_reports_are_accepted_without_required_headings(
    runner, isolated_dir, text
):
    from agentcad.cli import cli

    result = runner.invoke(cli, ["feedback", "--file", "-", "--local-only"], input=text)
    assert result.exit_code == 0
    data = payload(result)
    assert data["remote"] == "skipped"
    assert (
        json.loads(Path(data["bundle_file"]).read_text(encoding="utf-8"))["report"]
        == text
    )


@pytest.mark.parametrize(
    "args,stdin",
    [
        (["feedback", "note", "--file", "-"], "report"),
        (["feedback", "note", "--template"], None),
        (["feedback", "--file", "-", "--template"], "report"),
        (["feedback", "--output", "report.md"], None),
        (["feedback", "--file", "-"], "  \n"),
        (["feedback", "  \n"], None),
        (["feedback", "note", "--max-entries", "0"], None),
    ],
)
def test_invalid_inputs_do_not_submit_or_create_bundle(
    runner, isolated_dir, monkeypatch, args, stdin
):
    from agentcad.cli import cli

    monkeypatch.setattr(
        feedback_mod, "_send_remote", lambda _: pytest.fail("sent invalid feedback")
    )
    result = runner.invoke(cli, args, input=stdin)
    assert result.exit_code != 0
    assert payload(result)["status"] == "error"
    assert not list(isolated_dir.glob(".agentcad/feedback/*.json"))


def test_invalid_utf8_report_is_actionable_and_leaves_source_untouched(
    runner, isolated_dir
):
    from agentcad.cli import cli

    source = isolated_dir / "legacy.md"
    source.write_bytes(b"\xff\xfelegacy text")
    result = runner.invoke(cli, ["feedback", "--file", str(source)])
    assert result.exit_code != 0
    assert "UTF-8" in payload(result)["message"]
    assert source.read_bytes() == b"\xff\xfelegacy text"
    assert not list(isolated_dir.glob(".agentcad/feedback/*.json"))


def test_report_is_saved_before_network_failure(runner, isolated_dir, monkeypatch):
    from agentcad.cli import cli

    def fail_after_save(bundle):
        files = list(isolated_dir.glob(".agentcad/feedback/*.json"))
        assert len(files) == 1
        assert json.loads(files[0].read_text(encoding="utf-8"))["report"] == REPORT
        return {"err": "timed out", "discord": None, "neon_row_id": None}

    monkeypatch.setattr(feedback_mod, "_send_remote", fail_after_save)
    result = runner.invoke(cli, ["feedback", "--file", "-"], input=REPORT)
    data = payload(result)
    assert data["status"] == "partial"
    assert "bundle" not in data
    assert (
        json.loads(Path(data["bundle_file"]).read_text(encoding="utf-8"))["report"]
        == REPORT
    )


def test_413_reduces_logs_without_truncating_report(runner, isolated_dir, monkeypatch):
    from agentcad.cli import cli

    runner.invoke(cli, ["init", "--name", "feedback-project", "--no-agent-setup"])
    runner.invoke(cli, ["context"])
    calls = []

    def send(bundle):
        assert bundle["report"] == REPORT
        calls.append(len(bundle["session_log"]))
        return {
            "err": "HTTP Error 413" if calls[-1] > 1 else None,
            "discord": "ok",
            "neon_row_id": 9,
        }

    monkeypatch.setattr(feedback_mod, "_send_remote", send)
    result = runner.invoke(
        cli, ["feedback", "--file", "-", "--max-entries", "2"], input=REPORT
    )
    assert payload(result)["status"] == "success"
    assert calls == [2, 1]
    saved = json.loads(Path(payload(result)["bundle_file"]).read_text(encoding="utf-8"))
    assert saved["report"] == REPORT and len(saved["session_log"]) == 1


def test_two_reports_in_one_second_keep_both_bundles(runner, isolated_dir, monkeypatch):
    from agentcad.cli import cli

    class FixedClock:
        @staticmethod
        def now(tz):
            return datetime(2026, 11, 8, tzinfo=timezone.utc)

    monkeypatch.setattr(feedback_mod, "datetime", FixedClock)
    first = payload(
        runner.invoke(cli, ["feedback", "--file", "-", "--local-only"], input="first")
    )
    second = payload(
        runner.invoke(cli, ["feedback", "--file", "-", "--local-only"], input="second")
    )
    assert first["bundle_file"] != second["bundle_file"]
    assert (
        json.loads(Path(first["bundle_file"]).read_text(encoding="utf-8"))["report"]
        == "first"
    )
    assert (
        json.loads(Path(second["bundle_file"]).read_text(encoding="utf-8"))["report"]
        == "second"
    )


def test_template_does_not_initialize_a_configured_build_root(runner, isolated_dir):
    from agentcad.cli import cli

    result = runner.invoke(
        cli,
        [
            "feedback",
            "--template",
            "--output",
            "report.md",
            "--build-dir",
            "unused build",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert (isolated_dir / "report.md").exists()
    assert not (isolated_dir / "unused build").exists()


def test_report_uses_selected_build_root_and_legacy_note_still_has_bundle(
    runner, isolated_dir
):
    from agentcad.cli import cli

    runner.invoke(
        cli,
        [
            "init",
            "--name",
            "feedback-root",
            "--build-dir",
            "build space",
            "--no-agent-setup",
        ],
    )
    report = runner.invoke(
        cli,
        ["feedback", "--file", "-", "--local-only", "--build-dir", "build space"],
        input=REPORT,
    )
    assert report.exit_code == 0, report.stdout
    assert Path(payload(report)["bundle_file"]).is_relative_to(
        isolated_dir / "build space"
    )
    note = runner.invoke(
        cli, ["feedback", "small note", "--local-only", "--build-dir", "build space"]
    )
    assert payload(note)["bundle"]["summary"] == "small note"


def test_template_next_actions_preserve_explicit_build_root(runner, isolated_dir):
    runner.invoke(
        cli,
        [
            "init",
            "--name",
            "selected",
            "--build-dir",
            "build space",
            "--no-agent-setup",
        ],
    )
    guide = payload(runner.invoke(cli, ["feedback", "--build-dir", "build space"]))
    generate = shlex.split(guide["next_actions"][0])[1:]
    template = payload(runner.invoke(cli, generate))
    Path(template["template_file"]).write_text(
        "A report for the selected project.", encoding="utf-8"
    )
    submit = shlex.split(template["next_actions"][0])[1:] + ["--local-only"]
    result = runner.invoke(cli, submit)
    assert result.exit_code == 0, result.stdout
    assert Path(payload(result)["bundle_file"]).is_relative_to(
        isolated_dir / "build space"
    )
    assert not (isolated_dir / ".agentcad").exists()
