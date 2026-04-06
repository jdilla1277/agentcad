import json
import time
from pathlib import Path

from agentcad.session_log import SessionLogger


def test_log_entry_written_to_file(tmp_path):
    logger = SessionLogger(tmp_path)
    logger.log("run", {"script": "box.py", "--output": "v1"}, {"command": "run", "status": "success", "version": 1})

    log_file = tmp_path / ".agentcad" / "session.jsonl"
    assert log_file.exists()

    entries = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["command"] == "run"
    assert entry["args"] == {"script": "box.py", "--output": "v1"}
    assert entry["result"]["status"] == "success"
    assert "timestamp" in entry


def test_multiple_entries_appended(tmp_path):
    logger = SessionLogger(tmp_path)
    logger.log("init", {}, {"command": "init", "status": "success"})
    logger.log("run", {"script": "a.py"}, {"command": "run", "status": "failed", "error": "syntax"})

    log_file = tmp_path / ".agentcad" / "session.jsonl"
    entries = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
    assert len(entries) == 2
    assert entries[0]["command"] == "init"
    assert entries[1]["command"] == "run"


def test_creates_dot_agentcad_dir(tmp_path):
    logger = SessionLogger(tmp_path)
    logger.log("init", {}, {"command": "init", "status": "success"})
    assert (tmp_path / ".agentcad").is_dir()


def test_get_recent_returns_last_n(tmp_path):
    logger = SessionLogger(tmp_path)
    for i in range(10):
        logger.log("run", {"i": i}, {"command": "run", "status": "success", "version": i})

    recent = logger.get_recent(3)
    assert len(recent) == 3
    assert recent[0]["args"]["i"] == 7
    assert recent[2]["args"]["i"] == 9


def test_get_recent_returns_all_if_fewer_than_n(tmp_path):
    logger = SessionLogger(tmp_path)
    logger.log("init", {}, {"command": "init", "status": "success"})

    recent = logger.get_recent(50)
    assert len(recent) == 1


def test_get_recent_empty_log(tmp_path):
    logger = SessionLogger(tmp_path)
    recent = logger.get_recent(10)
    assert recent == []


def test_friction_signals_counts_errors(tmp_path):
    logger = SessionLogger(tmp_path)
    logger.log("run", {}, {"command": "run", "status": "success"})
    logger.log("run", {}, {"command": "run", "status": "failed", "error": "bad"})
    logger.log("run", {}, {"command": "run", "status": "error", "message": "missing"})
    logger.log("run", {}, {"command": "run", "status": "validation_error"})

    signals = logger.friction_signals()
    assert signals["total_commands"] == 4
    assert signals["errors"] == 3  # failed + error + validation_error
    assert signals["successes"] == 1


def test_friction_signals_detects_retries(tmp_path):
    """Consecutive calls to the same command = retries."""
    logger = SessionLogger(tmp_path)
    logger.log("run", {"script": "a.py"}, {"command": "run", "status": "failed"})
    logger.log("run", {"script": "a.py"}, {"command": "run", "status": "failed"})
    logger.log("run", {"script": "a.py"}, {"command": "run", "status": "success"})

    signals = logger.friction_signals()
    assert signals["retry_sequences"] == 1  # one sequence of retries
    assert signals["total_retries"] == 2  # two retries before success


def test_friction_signals_empty_log(tmp_path):
    logger = SessionLogger(tmp_path)
    signals = logger.friction_signals()
    assert signals["total_commands"] == 0
    assert signals["errors"] == 0
    assert signals["retry_sequences"] == 0
    assert signals["total_retries"] == 0


def test_log_entry_has_iso_timestamp(tmp_path):
    logger = SessionLogger(tmp_path)
    logger.log("init", {}, {"command": "init", "status": "success"})

    entries = logger.get_recent(1)
    ts = entries[0]["timestamp"]
    # Should be parseable as ISO format
    assert "T" in ts


def test_get_session_bundle(tmp_path):
    """Bundle combines agent message, session log, friction signals, and env."""
    logger = SessionLogger(tmp_path)
    logger.log("run", {}, {"command": "run", "status": "failed"})
    logger.log("run", {}, {"command": "run", "status": "success"})

    bundle = logger.get_session_bundle("fillet was confusing", max_entries=50)
    assert bundle["summary"] == "fillet was confusing"
    assert len(bundle["session_log"]) == 2
    assert bundle["friction_signals"]["total_commands"] == 2
    assert "python_version" in bundle["environment"]
    assert "agentcad_version" in bundle["environment"]
