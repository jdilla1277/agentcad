"""Parity guardrail — every test in tests/test_run.py must either:

  1. Have a name-matching counterpart in tests_b3d/test_run_end_to_end.py
     (after stripping the ``test_run_`` prefix to ``test_``), or
  2. Be listed in ``PARITY_MAP`` below — either pointing at the explicit
     b3d twin name, or marked ``cq_only:<reason>`` for runtime-agnostic /
     cq-specific tests that don't need a b3d twin.

Until cadquery is deprecated (Phase 7 of the migration), both runtimes
need side-by-side regression coverage on every behavior. This test
locks the rule in the environment per CLAUDE.md's PR Retro discipline:
"if a rule lives only in someone's head, it will eventually be
forgotten — encode it in the environment instead."

If you add a new test to ``test_run.py`` and this guardrail fails:
  - **Default action**: add a matching b3d twin to
    ``test_run_end_to_end.py`` (often a ~10-line port).
  - **If the test is genuinely runtime-agnostic** (CLI errors, env
    checks, view-helper unit tests): add an entry to ``PARITY_MAP``
    with ``cq_only:<short reason>``.
  - **If the b3d twin already exists with a different name** (legacy
    naming divergence): map cq → b3d in ``PARITY_MAP``.

This guardrail came out of the Phase 4 closeout (PRs #149-#158), where
auditing test_run.py vs tests_b3d/ surfaced that 97 of 102 cq tests had
no b3d twin — even though the runtime was already in production. The
gap was invisible because the rule lived only in the migration-plan
PRD, not in the test harness."""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_CQ_TEST_FILE = _REPO_ROOT / "tests" / "test_run.py"
_B3D_TEST_FILE = _REPO_ROOT / "tests_b3d" / "test_run_end_to_end.py"

_TEST_DEF = re.compile(r"^(?:    )?def (test_\w+)\(", re.MULTILINE)


# Explicit parity decisions. Either:
#   - a b3d test name (the equivalent twin), or
#   - "cq_only:<reason>" (no b3d coverage needed, with a one-line why).
#
# Add to this map ONLY when the b3d twin uses a different name (legacy
# divergence) or the test is genuinely runtime-agnostic. Default action
# for new test_run.py tests is to add a matching-name b3d twin instead.
PARITY_MAP: dict[str, str] = {
    # ---- genuinely cq-only ----
    "test_render_unified_empty_parts_payload_when_none":
        "cq_only:unit test on view helper, runtime-agnostic",
    "test_render_unified_includes_export_gif_button":
        "cq_only:unit test on view helper, runtime-agnostic",
    "test_render_unified_pauses_rotation_on_viewer_interaction":
        "cq_only:unit test on view helper, runtime-agnostic",
    "test_render_unified_keeps_preserve_drawing_buffer":
        "cq_only:unit test on view helper, runtime-agnostic",
    "test_run_brep_api_error_enriched":
        "cq_only:cqgi.CQModel monkeypatch — no analogous error-enrichment hook on b3d (track in a follow-up if b3d needs OCP error guidance)",
    "test_run_python_version_ok_no_error":
        "cq_only:env check, runtime-agnostic CLI wiring",
    "test_run_python_version_too_new_error":
        "cq_only:env check, runtime-agnostic CLI wiring",
    "test_end_to_end_workflow":
        "cq_only:smoke test exercising all commands; pieces covered piecewise on b3d",
    "test_run_emits_json_error_on_unexpected_exception_after_script_start":
        "cq_only:CLI catch-all error wrapper (#194); monkeypatches compute_metrics — runtime-agnostic, the wrapper sits above the runtime dispatch",
    "test_run_no_manifest_error":
        "cq_only:CLI error orthogonal to runtime",
    "test_run_script_not_found_error":
        "cq_only:CLI error orthogonal to runtime",
    "test_run_rejects_unsupported_export_format_for_cad_file_input":
        "cq_only:--export validation before CAD-file suffix dispatch — fires before any runtime is selected",
    "test_run_existing_version_dir_no_crash":
        "cq_only:CLI dir-collision behavior, runtime-agnostic — happy path covered by b3d test_creates_versioned_output_step",

    # ---- legacy name divergences (b3d twin under a different name) ----
    "test_run_auto_increments_version": "test_second_run_increments_version",
    "test_run_copies_script_to_version_dir": "test_copies_script_into_version_dir",
    "test_run_creates_version_directory": "test_creates_versioned_output_step",
    "test_run_creates_meta_json": "test_meta_json_has_runtime_and_metrics",
    "test_run_default_includes_preview": "test_preview_on_by_default",
    "test_run_dry_run_no_disk_artifacts": "test_no_disk_artifacts",
    "test_run_dry_run_no_version_consumed": "test_no_disk_artifacts",
    "test_run_dry_run_returns_metrics": "test_returns_metrics",
    "test_run_label_in_directory_name": "test_creates_versioned_output_step",
    "test_run_meta_json_includes_metrics": "test_meta_json_has_runtime_and_metrics",
    "test_run_multi_show_object_warnings_key_is_list_type_if_present":
        "test_multi_show_warnings_key_is_list_type_if_present",
    "test_run_no_preview_flag_suppresses_preview": "test_no_preview_suppresses",
    "test_run_no_show_object_caught_by_validation": "test_no_show_object_fails_validation",
    "test_run_params_override_changes_output": "test_override_dims_grow",
    "test_run_params_unknown_parameter_error": "test_unknown_param_fails_fast",
    "test_run_preview_produces_png": "test_preview_on_by_default",
    "test_run_produces_step_file": "test_creates_versioned_output_step",
    "test_run_success_includes_metrics": "test_meta_json_has_runtime_and_metrics",
    "test_run_success_json": "test_meta_json_has_runtime_and_metrics",
    "test_run_updates_manifest_versions": "test_second_run_increments_version",
    "test_run_with_export_and_render": "test_export_and_render",
    "test_run_with_export_glb": "test_glb_export",
    "test_run_with_export_json_response": "test_export_json_response_paths",
    "test_run_with_export_meta_json": "test_export_meta_json",
    "test_run_with_export_multiple": "test_multiple_formats",
    "test_run_with_export_obj": "test_obj_export",
    "test_run_with_export_stl": "test_stl_export",
    "test_run_with_render_all": "test_render_all",
    "test_run_with_render_json_response": "test_render_metadata_in_json",
    "test_run_with_render_meta_json": "test_render_meta_json",
    "test_run_with_render_multiple_views": "test_multiple_named_views",
    "test_run_with_render_produces_png": "test_iso_render_produces_png",
}


def _extract_test_names(path: Path) -> set[str]:
    return set(_TEST_DEF.findall(path.read_text()))


def _suffix_after_test_run(name: str) -> str:
    """Strip ``test_run_`` if present, else ``test_``. Used for default
    name-matching: ``test_run_named_parts_emits_parts_array`` (cq) →
    ``named_parts_emits_parts_array`` matches ``test_named_parts_emits_parts_array``
    (b3d) after the same strip."""
    for prefix in ("test_run_", "test_"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


@pytest.fixture(scope="module")
def _cq_tests() -> set[str]:
    return _extract_test_names(_CQ_TEST_FILE)


@pytest.fixture(scope="module")
def _b3d_tests() -> set[str]:
    return _extract_test_names(_B3D_TEST_FILE)


def test_every_cq_test_has_a_b3d_twin_or_explicit_parity_entry(_cq_tests, _b3d_tests):
    """Every test_run.py test must either name-match a b3d test (after
    suffix-stripping) or appear in PARITY_MAP with a documented decision."""
    b3d_suffixes = {_suffix_after_test_run(n) for n in _b3d_tests}

    missing = []
    for cq in sorted(_cq_tests):
        # 1. Default match: same suffix in b3d.
        if _suffix_after_test_run(cq) in b3d_suffixes:
            continue
        # 2. Explicit parity decision.
        if cq in PARITY_MAP:
            continue
        missing.append(cq)

    if missing:
        msg = (
            f"\n{len(missing)} test(s) in tests/test_run.py have no b3d "
            f"counterpart:\n"
            + "\n".join(f"  - {n}" for n in missing)
            + "\n\nFix one of these ways:\n"
            "  • Add a matching-name twin to "
            "tests_b3d/test_run_end_to_end.py (preferred — see "
            "examples in TestNamedParts, TestMultiShow, TestParams etc.).\n"
            "  • If the test is genuinely runtime-agnostic or cq-specific, "
            "add it to PARITY_MAP in this file as 'cq_only:<reason>'.\n"
            "  • If a b3d twin already exists under a different name, map "
            "cq → b3d in PARITY_MAP.\n\n"
            "See the module docstring for the rationale (Phase 4 closeout, "
            "PRs #149-#158)."
        )
        raise AssertionError(msg)


def test_parity_map_entries_are_valid(_b3d_tests):
    """Every PARITY_MAP value must either be ``cq_only:<reason>`` or
    name an actual b3d test. Catches typos / stale entries when a b3d
    test is renamed without updating the map."""
    invalid: list[str] = []
    for cq, target in PARITY_MAP.items():
        if target.startswith("cq_only:"):
            # Require a non-empty reason after the colon.
            reason = target[len("cq_only:"):].strip()
            if not reason:
                invalid.append(f"{cq}: 'cq_only:' must include a reason")
            continue
        if target not in _b3d_tests:
            invalid.append(
                f"{cq} → {target!r}: target b3d test not found "
                f"(renamed? deleted?)"
            )
    if invalid:
        raise AssertionError(
            "\nPARITY_MAP entries point at non-existent b3d tests or have "
            "missing reasons:\n" + "\n".join(f"  - {x}" for x in invalid)
        )


def test_parity_map_has_no_dead_entries(_cq_tests):
    """Every PARITY_MAP key must reference a real cq test. Catches
    entries left behind when a cq test is renamed or deleted."""
    dead = sorted(set(PARITY_MAP) - _cq_tests)
    if dead:
        raise AssertionError(
            "\nPARITY_MAP keys reference non-existent cq tests "
            "(rename/delete?):\n" + "\n".join(f"  - {x}" for x in dead)
        )
