"""JSON-on-stdout contract tests for every OCCT-touching command.

The bug class this catches: OCP / OCCT writes diagnostics directly to
file descriptor 1, bypassing Python's sys.stdout buffer entirely. On a
malformed STEP, those C-level writes leak onto stdout *before* our
JSON, and `json.loads(stdout)` then raises JSONDecodeError. CliRunner
can't see this — it captures `sys.stdout` at the Python level — only
a real subprocess does.

We've hit this bug class four times during M60:
  - Phase 1a: caught for `inspect` (PR #113).
  - Phase 2c: caught for `render`, `export`, `view` after consolidating
    the STEP loader (PR #123 follow-up).
  - Phase 4c follow-up: caught when load_cad_shape's ValueError escaped
    those same three commands (PR #125 follow-up).

Each fix has been one-off. This module is the generic harness — every
OCCT-touching command runs via real subprocess on a malformed STEP and
must produce parseable JSON on stdout. New commands that touch OCCT
add one entry to ``_COMMANDS_TO_CHECK`` and inherit the guard.

If a future load-shape callsite forgets to silence fd-1 (or an
exception escapes), this is where it'll fail.
"""
import json
import subprocess
import sys
from pathlib import Path

import cadquery as cq
import pytest
from cadquery import exporters


def _agentcad_bin() -> Path:
    candidate = Path(sys.executable).parent / "agentcad"
    if not candidate.exists():
        pytest.skip(f"agentcad CLI not found at {candidate}")
    return candidate


def _run(*args, cwd=None):
    return subprocess.run(
        [str(_agentcad_bin()), *args],
        capture_output=True, text=True, cwd=str(cwd) if cwd else None,
    )


@pytest.fixture
def truncated_step(tmp_path):
    """A STEP that has the right magic bytes but cuts off mid-parse —
    triggers OCCT's parser to fail in the place that's historically
    leaked diagnostics. Each test run gets a fresh copy so cleanup
    between parametrize entries is automatic."""
    full = tmp_path / "_full.step"
    exporters.export(cq.Workplane("XY").box(10, 10, 10), str(full))
    truncated = tmp_path / "truncated.step"
    truncated.write_text(full.read_text()[: len(full.read_text()) // 2])
    return truncated


@pytest.fixture
def initialized_dir(tmp_path):
    """A directory with an initialized agentcad project — required by
    commands like `import` and `run` that need a manifest to write into."""
    result = _run("init", cwd=tmp_path)
    assert result.returncode == 0, f"init failed: {result.stdout} {result.stderr}"
    return tmp_path


# Each entry: (test_id, argv_template, needs_init).
# argv_template uses "{step}" as a placeholder for the truncated-STEP path.
# Every command listed here parses STEP via OCCT and must respect the
# JSON-on-stdout contract. New OCCT-touching commands go here.
_COMMANDS_TO_CHECK = [
    ("inspect",         ["inspect", "{step}"],                          False),
    ("inspect_ids",     ["inspect", "{step}", "--ids"],                 False),
    ("measure",         ["measure", "{step}"],                          False),
    ("measure_features", ["measure", "{step}", "--features"],           False),
    ("check_spec",      ["check-spec", "{step}", "{spec}"],             False),
    ("render",          ["render", "{step}", "--view", "iso"],          False),
    ("export_glb",      ["export", "{step}", "--format", "glb"],        False),
    ("view",            ["view", "{step}"],                             False),
    ("import",          ["import", "{step}", "--init"],                 False),
    ("run_dispatch",    ["run", "{step}", "--output", "dispatched"],    True),
]


@pytest.mark.parametrize("test_id,argv_template,needs_init", _COMMANDS_TO_CHECK,
                         ids=[c[0] for c in _COMMANDS_TO_CHECK])
def test_malformed_step_produces_clean_json_on_stdout(
    test_id, argv_template, needs_init, truncated_step, tmp_path
):
    """Load-bearing contract: every OCCT-touching command, when handed a
    malformed STEP via real subprocess, must emit parseable JSON on
    stdout. No leaked OCCT diagnostics, no Python tracebacks on stderr.

    If this fails, the failure mode is one of:
      - The command lost its `silence_native_stdout()` wrap somewhere.
      - A new exception type escaped the command's try/except envelope.
      - The command was added without going through `step_io.load_cad_shape`.
    """
    if needs_init:
        init_result = _run("init", cwd=tmp_path)
        assert init_result.returncode == 0, init_result.stderr

    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "features": [
            {"name": "holes", "type": "cylinder", "diameter_mm": 6, "count": 2}
        ]
    }))
    argv = [
        a.replace("{step}", str(truncated_step)).replace("{spec}", str(spec))
        for a in argv_template
    ]
    result = _run(*argv, cwd=tmp_path)

    # The contract: stdout is parseable JSON, full stop.
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"{test_id}: stdout was not parseable JSON.\n"
            f"  exit_code={result.returncode}\n"
            f"  stdout (first 500): {result.stdout[:500]!r}\n"
            f"  stderr (first 500): {result.stderr[:500]!r}\n"
            f"  decode error: {exc}"
        )

    # No Python traceback should leak — those signal an unhandled exception
    # in the command, which means the command's contract is broken.
    assert "Traceback" not in result.stderr, (
        f"{test_id}: Python traceback leaked to stderr — command let an "
        f"exception escape unhandled.\n  stderr: {result.stderr[:500]!r}"
    )

    # The JSON must at minimum identify itself with command + status fields.
    assert "command" in parsed, f"{test_id}: response missing `command` key: {parsed}"
    assert "status" in parsed, f"{test_id}: response missing `status` key: {parsed}"
    # status should signal failure on a malformed input — exact value varies
    # by command (malformed / error / empty are all acceptable).
    assert parsed["status"] in {"malformed", "error", "empty", "failed"}, \
        f"{test_id}: unexpected success status on malformed STEP: {parsed}"


# --- real-world fixture sanity check ---------------------------------------
# Item 2 of the test-infra-hardening PR: confirm the Pump Manifold fixture
# is in place and can be loaded. Also serves as the entry point for future
# regression tests on real-part topology.

class TestRealWorldFixture:
    def test_pump_manifold_loads_via_inspect_subprocess(self, real_world_step, tmp_path):
        """Real Fusion-exported AP214 STEP loads cleanly via subprocess,
        with all the expected complexity (>100 faces, asymmetric
        face_orientations, valid closed solid). Locks in the verified
        e2e behavior from PR #127 retro."""
        result = _run("inspect", str(real_world_step), "--ids", cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        assert parsed["format_detected"] == "step"
        # Real-part topology characteristics — won't happen on synthetic boxes.
        assert parsed["face_count"] > 50
        assert parsed["is_valid"] is True
        assert parsed["free_edge_count"] == 0
        # The face_orientations note from the e2e retro should fire on this
        # exact file (asymmetric forward/reversed counts).
        fwd = parsed["face_orientations"]["forward"]
        rev = parsed["face_orientations"]["reversed"]
        if fwd != rev:
            notes = parsed.get("notes", [])
            assert any(
                "orientation" in n.lower() or "forward" in n.lower()
                for n in notes
            ), (
                "Pump Manifold has asymmetric face_orientations but no "
                f"orientation note fired. forward={fwd}, reversed={rev}, "
                f"notes={notes}"
            )
