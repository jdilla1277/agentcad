import json
from pathlib import Path

from click.testing import CliRunner
from agentcad.cli import cli


def test_feedback_subcommand_registered(runner):
    result = runner.invoke(cli, ["feedback", "--help"])
    assert result.exit_code == 0
    assert "feedback" in result.output.lower()


def test_feedback_requires_message(runner, isolated_dir):
    result = runner.invoke(cli, ["feedback"])
    assert result.exit_code != 0


def test_feedback_outputs_bundle_json(runner, isolated_dir):
    # Create a session log first
    (isolated_dir / ".agentcad").mkdir()
    log_file = isolated_dir / ".agentcad" / "session.jsonl"
    entry = json.dumps({
        "timestamp": "2026-03-31T00:00:00+00:00",
        "command": "run",
        "args": {"script": "box.py"},
        "result": {"command": "run", "status": "failed", "error": "syntax error"},
    })
    log_file.write_text(entry + "\n")

    result = runner.invoke(cli, ["feedback", "fillet was confusing"])
    assert result.exit_code == 0

    output = json.loads(result.output)
    assert output["command"] == "feedback"
    assert output["status"] == "success"
    assert output["bundle"]["summary"] == "fillet was confusing"
    assert len(output["bundle"]["session_log"]) == 1
    assert output["bundle"]["friction_signals"]["total_commands"] == 1


def test_feedback_writes_bundle_file(runner, isolated_dir):
    result = runner.invoke(cli, ["feedback", "needs better docs"])
    assert result.exit_code == 0

    output = json.loads(result.output)
    bundle_path = output["bundle_file"]
    assert Path(bundle_path).exists()

    bundle = json.loads(Path(bundle_path).read_text())
    assert bundle["summary"] == "needs better docs"


def test_feedback_works_with_no_session_log(runner, isolated_dir):
    """Feedback should work even if no commands have been run yet."""
    result = runner.invoke(cli, ["feedback", "just a thought"])
    assert result.exit_code == 0

    output = json.loads(result.output)
    assert output["bundle"]["session_log"] == []
    assert output["bundle"]["friction_signals"]["total_commands"] == 0


def test_feedback_bundle_includes_environment(runner, isolated_dir):
    result = runner.invoke(cli, ["feedback", "test env"])
    assert result.exit_code == 0

    output = json.loads(result.output)
    env = output["bundle"]["environment"]
    assert "python_version" in env
    assert "agentcad_version" in env
    assert "platform" in env


def test_feedback_max_entries_flag(runner, isolated_dir):
    """--max-entries controls how many session log entries are included."""
    (isolated_dir / ".agentcad").mkdir()
    log_file = isolated_dir / ".agentcad" / "session.jsonl"
    lines = []
    for i in range(20):
        lines.append(json.dumps({
            "timestamp": f"2026-03-31T00:{i:02d}:00+00:00",
            "command": "run",
            "args": {"i": i},
            "result": {"command": "run", "status": "success"},
        }))
    log_file.write_text("\n".join(lines) + "\n")

    result = runner.invoke(cli, ["feedback", "too many", "--max-entries", "5"])
    assert result.exit_code == 0

    output = json.loads(result.output)
    assert len(output["bundle"]["session_log"]) == 5
