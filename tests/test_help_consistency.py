"""Cross-check that --help and `agentcad docs` claims about the global
default runtime match ``dispatch.DEFAULT_RUNTIME``.

Without this guardrail, stale text survives default-flips silently —
post-#163's flip to build123d, `cli.py`'s ``_BRIEFING`` still said
"docs default to cadquery" and `docs runtimes` still said "will flip
after Phase 6," and the 40 sub-agent friction trials in PR #194 had
to surface it. A doc-vs-constant test in CI catches the same class
of bug before it ships.
"""
from __future__ import annotations

import json
import re

from click.testing import CliRunner

from agentcad.cli import cli
from agentcad.runners import dispatch


def _help_text() -> str:
    """Capture the full --help output, whitespace-normalized.

    Click hard-wraps lines for terminal width, so the literal string
    "docs default to cadquery" can appear as "docs default to\\ncadquery".
    Collapsing all whitespace runs to single spaces makes the
    substring checks below robust to line-wrap reflow.
    """
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    return re.sub(r"\s+", " ", result.output)


def _docs_runtimes_content() -> str:
    """The 'Choosing a runtime' section, runtime-agnostic."""
    result = CliRunner().invoke(cli, ["docs", "runtimes"])
    assert result.exit_code == 0
    return json.loads(result.output)["content"]


class TestHelpConsistency:
    """Doc text must agree with the dispatcher's actual ``DEFAULT_RUNTIME``."""

    def test_help_does_not_mislabel_default_runtime(self):
        """`agentcad --help` must not claim the non-default runtime is
        the default. Catches stale text like "docs default to cadquery"
        that survived #163's flip to build123d."""
        text = _help_text()
        default = dispatch.DEFAULT_RUNTIME
        other = "build123d" if default == "cadquery" else "cadquery"

        # Phrasings that have claimed the wrong default in the past.
        # If new ones appear, add them here — this is a content guardrail,
        # not a fancy parser.
        bad_phrasings = [
            f"docs default to {other}",
            f"{other} fallback",
            f"{other} (the legacy default)",
            f"> {other} fallback",  # the "Dispatch order: ... > cadquery fallback" line
            "will flip after Phase 6",
        ]
        for phrase in bad_phrasings:
            assert phrase not in text, (
                f"--help still says {phrase!r}; "
                f"DEFAULT_RUNTIME is {default!r}, "
                f"so this is stale and will mislead agents."
            )

    def test_docs_runtimes_does_not_mislabel_default_runtime(self):
        """`agentcad docs runtimes` must not claim the non-default runtime
        is the global fallback. Same shape as the --help test; both
        surfaces are agent-readable."""
        text = _docs_runtimes_content()
        default = dispatch.DEFAULT_RUNTIME
        other = "build123d" if default == "cadquery" else "cadquery"

        bad_phrasings = [
            f"Fallback: {other}",
            f"Global default: {other}",
            f"the legacy default; will flip after Phase 6",
            "wins if set",
        ]
        for phrase in bad_phrasings:
            assert phrase not in text, (
                f"`docs runtimes` still says {phrase!r}; "
                f"DEFAULT_RUNTIME is {default!r}."
            )

    def test_docs_runtimes_names_default_runtime_as_global_default(self):
        """The runtimes section must name the actual ``DEFAULT_RUNTIME``
        as the global default — not by omission, by explicit text."""
        text = _docs_runtimes_content()
        default = dispatch.DEFAULT_RUNTIME
        assert f"Global default: {default}" in text, (
            f"`docs runtimes` should explicitly name {default!r} as the "
            f"global default so an agent reading this section knows "
            f"what they'll get without configuration."
        )
