#!/usr/bin/env python3
"""Recalculate sanitized GPT-5 Nano run totals without external dependencies."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RATES = {"input": 0.05, "cacheRead": 0.005, "output": 0.40}
FORBIDDEN_KEYS = {"bundle_dir", "sessionFile", "sessionId", "stack", "trial_id"}

RUNS = {
    "control": {
        "trials": "control-trials.jsonl",
        "summary": "control-summary.json",
    },
    "agentcad": {
        "trials": "agentcad-trials.jsonl",
        "summary": "agentcad-summary.json",
    },
}


def walk_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(walk_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(walk_keys(item) for item in value))
    return set()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def verify_run(label: str, paths: dict[str, str]) -> None:
    trials_path = ROOT / paths["trials"]
    summary = json.loads((ROOT / paths["summary"]).read_text())
    trials = load_jsonl(trials_path)

    assert len(trials) == 81, f"{label}: expected 81 trial records"
    assert len({trial["fixture_id"] for trial in trials}) == 81
    assert Counter(trial["task_type"] for trial in trials) == {
        "generation": 49,
        "editing": 32,
    }
    assert not (walk_keys(trials) & FORBIDDEN_KEYS)
    assert "/Users/" not in trials_path.read_text()

    tokens = Counter()
    recorded_cost = 0.0
    for trial in trials:
        tokens.update(trial["usage"]["tokens"])
        recorded_cost += trial["usage"]["cost"]

    usage = summary["usage"]
    assert tokens["input"] == usage["input_tokens"]
    assert tokens["output"] == usage["output_tokens"]
    assert tokens["cacheRead"] == usage["cached_input_tokens"]
    assert tokens["cacheWrite"] == usage["cache_write_tokens"]
    assert tokens["total"] == usage["total_tokens"]
    assert math.isclose(recorded_cost, usage["estimated_model_cost_usd"], abs_tol=1e-12)

    calculated_cost = sum(tokens[key] * rate / 1_000_000 for key, rate in RATES.items())
    assert math.isclose(calculated_cost, recorded_cost, abs_tol=1e-12)

    harness = summary["harness"]
    harness_failures = Counter(
        trial["harness"]["stage"]
        for trial in trials
        if trial["harness"]["status"] != "ok"
    )
    candidate_failures = Counter(
        trial["candidate"]["reason"]
        for trial in trials
        if trial["candidate"] and trial["candidate"]["status"] == "failed"
    )
    classifications = Counter(
        trial["submission"]["classification"]
        for trial in trials
        if trial["submission"]
    )
    assert sum(harness_failures.values()) == harness["harness_failures"]
    assert dict(harness_failures) == harness["harness_failures_by_stage"]
    assert sum(candidate_failures.values()) == harness["candidate_failures"]
    assert dict(candidate_failures) == harness["candidate_failures_by_reason"]
    for classification, count in classifications.items():
        assert harness["valid_outputs_by_classification"][classification] == count

    print(
        f"{label}: 81 records, {tokens['total']:,} tokens, "
        f"${recorded_cost:.8f}, evidence consistent"
    )


def main() -> None:
    for label, paths in RUNS.items():
        verify_run(label, paths)


if __name__ == "__main__":
    main()
