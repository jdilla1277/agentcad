import json
import shlex
import signal
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import click

from agentcad import file_detect
from agentcad.commands._daemon_routing import (
    maybe_route_through_daemon,
    maybe_spawn_daemon_for_next_run,
)
from agentcad.native_io import suppress_native_output

DEFAULT_ID_LIMIT = 100
DEFAULT_VALIDATION_TIMEOUT_S = 90.0
VALIDATION_TIMEOUT_ENV = "AGENTCAD_INSPECT_TIMEOUT_S"
LARGE_FILE_PROGRESS_BYTES = 5_000_000
VALIDATION_PHASES = (
    "native_load",
    "structural_validation",
    "topology_extraction",
    "feature_extraction",
)


class _InspectValidationTimeout(BaseException):
    """Keep the inspect budget outside broad native-parser catches."""


class _ValidationPhaseTracker:
    def __init__(self, budget_s: float | None, *, progress: bool):
        self.budget_s = budget_s
        self.progress = progress
        self.started_at = time.perf_counter()
        self.active_phase = None
        self.active_started_at = None
        self.entries = {
            name: {"status": "pending"} for name in VALIDATION_PHASES
        }

    @contextmanager
    def observe(self, phase: str):
        self.active_phase = phase
        self.active_started_at = time.perf_counter()
        if self.progress:
            click.echo(
                f"[agentcad] inspect phase: {phase.replace('_', ' ')}…",
                err=True,
            )
        try:
            yield
        except Exception as exc:
            self.entries[phase] = {
                "status": "failed",
                "duration_ms": self._active_duration_ms(),
                "message": f"{type(exc).__name__}: {exc}",
            }
            self._clear_active()
            raise
        else:
            duration_ms = self._active_duration_ms()
            if self.expired:
                self.mark_timeout(phase, duration_ms=duration_ms)
                raise _InspectValidationTimeout()
            self.entries[phase] = {
                "status": "success",
                "duration_ms": duration_ms,
            }
            self._clear_active()

    @property
    def expired(self) -> bool:
        return (
            self.budget_s is not None
            and time.perf_counter() - self.started_at >= self.budget_s
        )

    @property
    def elapsed_s(self) -> float:
        return round(time.perf_counter() - self.started_at, 6)

    def skip(self, phase: str, message: str) -> None:
        self.entries[phase] = {"status": "skipped", "message": message}

    def mark_timeout(
        self,
        phase: str | None = None,
        *,
        duration_ms: float | None = None,
    ) -> str:
        phase = phase or self.active_phase
        if phase is None:
            phase = next(
                (
                    name
                    for name, entry in self.entries.items()
                    if entry.get("status") == "pending"
                ),
                None,
            )
        if phase is None:
            phase = next(
                (
                    name
                    for name in reversed(VALIDATION_PHASES)
                    if self.entries[name].get("status") == "success"
                ),
                "native_load",
            )
        if phase in self.entries:
            self.entries[phase] = {
                "status": "timeout",
                "duration_ms": (
                    self._active_duration_ms()
                    if duration_ms is None
                    else duration_ms
                ),
            }
        for name, entry in self.entries.items():
            if entry.get("status") == "pending":
                self.skip(name, f"Not reached after {phase} timed out.")
        self._clear_active()
        return phase

    def _active_duration_ms(self) -> float:
        if self.active_started_at is None:
            return 0
        return round(
            (time.perf_counter() - self.active_started_at) * 1000,
            3,
        )

    def _clear_active(self) -> None:
        self.active_phase = None
        self.active_started_at = None


def _install_validation_timeout(tracker: _ValidationPhaseTracker):
    if tracker.budget_s is None:
        return None
    if threading.current_thread() is not threading.main_thread():
        return None
    if (
        not hasattr(signal, "SIGALRM")
        or not hasattr(signal, "setitimer")
        or not hasattr(signal, "getitimer")
    ):
        return None

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if 0 < previous_timer[0] <= tracker.budget_s:
        # A host-level deadline (for example pytest-timeout) is already
        # stricter. Do not mask or postpone it.
        return None

    def _handle_timeout(_signum, _frame):
        tracker.mark_timeout()
        raise _InspectValidationTimeout()

    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, tracker.budget_s)
    return previous_handler, previous_timer, time.perf_counter()


def _clear_validation_timeout(previous_state) -> None:
    if previous_state is None:
        return
    previous_handler, previous_timer, installed_at = previous_state
    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, previous_handler)
    if previous_timer[0] > 0:
        elapsed = time.perf_counter() - installed_at
        remaining = max(previous_timer[0] - elapsed, 0.000001)
        signal.setitimer(
            signal.ITIMER_REAL,
            remaining,
            previous_timer[1],
        )


@click.command("inspect")
@click.argument("file")
@click.option(
    "--ids", "with_ids", is_flag=True,
    help="Include per-feature ID lists (solids, faces, edges) for use with "
         "pick_face / pick_edge in edit scripts. IDs are 1-indexed and "
         "stable within a shape but not across edits.",
)
@click.option(
    "--summary", "with_summary", is_flag=True,
    help="Cluster faces and edges into semantic groups (planar by axis, "
         "cylindrical by axis+radius, edges by curve type). Compact "
         "alternative to --ids for parts where the per-feature payload "
         "would blow the context budget. Combine with --ids for both.",
)
@click.option(
    "--limit",
    "id_limit",
    type=click.IntRange(min=1),
    default=DEFAULT_ID_LIMIT,
    show_default=True,
    help="Maximum records per --ids list, and maximum IDs per summary cluster.",
)
@click.option(
    "--no-limit",
    is_flag=True,
    help="Return complete --ids and summary ID lists. Can be very large.",
)
@click.option(
    "--validate-only",
    is_flag=True,
    help="Load CAD and run structural validity checks without deep topology extraction.",
)
@click.option(
    "--validation-timeout",
    type=click.FloatRange(min=0),
    default=DEFAULT_VALIDATION_TIMEOUT_S,
    envvar=VALIDATION_TIMEOUT_ENV,
    show_envvar=True,
    show_default=True,
    help="Validation budget in seconds; 0 disables the timeout.",
)
@click.option("--no-daemon", is_flag=True, default=False, help="Skip daemon routing for this run, even if a daemon is running. Useful for debugging.")
def inspect_cmd(
    file,
    with_ids,
    with_summary,
    id_limit,
    no_limit,
    validate_only,
    validation_timeout,
    no_daemon,
):
    """Inspect any file. STEP/BREP get a full topology report; other formats
    get a structured 'recognized but not editable here' response. Never throws."""
    if validate_only and (with_ids or with_summary):
        raise click.UsageError(
            "--validate-only cannot be combined with --ids or --summary; "
            "remove --validate-only to request deep feature extraction."
        )
    # Try routing through daemon. Exits before returning if reachable.
    argv = ["inspect", file]
    if with_ids:
        argv.append("--ids")
    if with_summary:
        argv.append("--summary")
    if id_limit != DEFAULT_ID_LIMIT:
        argv.extend(["--limit", str(id_limit)])
    if no_limit:
        argv.append("--no-limit")
    if validate_only:
        argv.append("--validate-only")
    argv.extend(["--validation-timeout", str(validation_timeout)])
    maybe_route_through_daemon(argv, no_daemon=no_daemon)

    file_path = Path(file)
    detection = file_detect.detect_file_type(file_path)
    category = detection["category"]

    if category == file_detect.MISSING:
        _emit({
            "command": "inspect", "status": "error",
            "format_detected": None,
            "message": f"File '{file}' not found.",
            "suggestion": (
                "Check the path — the file does not exist at this location. "
                "If you expected it from a download, verify the source returned the file."
            ),
        }, exit_code=1)
        return

    if category == file_detect.NOT_A_FILE:
        reason = detection.get("reason", "not_a_regular_file")
        _emit({
            "command": "inspect", "status": "error",
            "format_detected": None,
            "message": f"Path '{file}' is not a regular file ({reason}).",
            "suggestion": "Pass a path to a real CAD file.",
        }, exit_code=1)
        return

    if category == file_detect.EMPTY:
        _emit({
            "command": "inspect", "status": "empty",
            "format_detected": detection.get("format"),
            "extension": detection.get("extension"),
            "size_bytes": 0,
            "message": f"File '{file}' is empty.",
            "suggestion": "Re-export from your CAD tool; the file may be a failed write.",
        }, exit_code=1)
        return

    if category == file_detect.MALFORMED:
        _emit_malformed(file, detection)
        return

    if category == file_detect.UNKNOWN_FORMAT:
        _emit({
            "command": "inspect", "status": "unknown_format",
            "format_detected": detection.get("format"),
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "message": (
                f"agentcad doesn't recognize extension '{detection.get('extension')}'."
            ),
            "suggestion": (
                "Supported formats: .step .stp .brep (full edit), .stl (inspect-only). "
                "If this is a CAD file, re-export as STEP."
            ),
        }, exit_code=0)
        return

    if category == file_detect.DISPLAY_FORMAT:
        fmt = detection["format"]
        _emit({
            "command": "inspect", "status": "display_format",
            "format_detected": fmt,
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "message": (
                f"{fmt.upper()} is an export/display format, not a source CAD format."
            ),
            "suggestion": (
                "Ask the original tool to export STEP — that's the format agentcad edits."
            ),
        }, exit_code=0)
        return

    if category == file_detect.TIER2_RECOGNIZED:
        fmt = detection["format"]
        _emit({
            "command": "inspect", "status": "recognized_deferred",
            "format_detected": fmt,
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "message": (
                f"Format '{fmt}' is recognized but not in agentcad's v0 edit scope."
            ),
            "suggestion": (
                "Convert to STEP (lossless for B-rep formats) or run "
                "`agentcad feedback` if support would unblock you."
            ),
        }, exit_code=0)
        return

    if category == file_detect.TIER1_MESH:
        _emit({
            "command": "inspect", "status": "limited",
            "format_detected": detection["format"],
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "editable": False,
            "message": (
                "STL is a triangle mesh — agentcad's edit pipeline is B-rep. "
                "Faces and edges in a mesh are facets, not semantic features, so "
                "fillet, shell, and face-extrude don't apply."
            ),
            "suggestion": (
                "Recreate parametrically (run `agentcad docs patterns` for examples), "
                "or ask the original tool to export STEP."
            ),
        }, exit_code=0)
        return

    if category == file_detect.TIER0_BREP:
        _inspect_tier0(
            str(file_path.absolute()),
            detection,
            with_ids=with_ids,
            with_summary=with_summary,
            id_limit=None if no_limit else id_limit,
            validate_only=validate_only,
            validation_timeout=(
                None if validation_timeout == 0 else validation_timeout
            ),
        )
        # Fork off the warm process as the daemon — only the Tier 0 path
        # paid the OCP cost worth keeping around. Idempotent on its own.
        maybe_spawn_daemon_for_next_run(no_daemon=no_daemon)
        return

    # Defensive: no other categories should reach here.
    _emit({
        "command": "inspect", "status": "error",
        "format_detected": None,
        "message": f"Unhandled file category '{category}'.",
    }, exit_code=1)


def _emit(payload: dict, exit_code: int = 0) -> None:
    click.echo(json.dumps(payload))
    if exit_code != 0:
        sys.exit(exit_code)


def _emit_malformed(file: str, detection: dict) -> None:
    expected = (
        detection.get("expected_format")
        or detection.get("extension")
        or "CAD"
    )
    expected_name = str(expected).upper()
    actual = detection.get("format")
    if actual == "html":
        reason = (
            f"{Path(file).name} contains HTML instead of {expected_name} CAD, "
            "likely because a download saved an error page."
        )
    else:
        reason = (
            f"{Path(file).name} claims to be {expected_name} CAD but its "
            f"content looks like {actual or 'an unknown format'}."
        )
    _emit_malformed_recovery(file, detection, reason)


def _malformed_recovery_action() -> str:
    """Return one executable route back to kernel-generated CAD."""
    for script_name in ("edit.py", "script.py"):
        if Path(script_name).is_file():
            run_action = (
                f"agentcad run {shlex.quote(script_name)} "
                "--label recovered"
            )
            if not Path("agentcad.json").is_file():
                return "agentcad init --name recovered && " + run_action
            return run_action
    return "agentcad docs quickstart"


def _emit_malformed_recovery(
    file: str,
    detection: dict,
    reason: str,
) -> None:
    """Emit one clean malformed-CAD error and one executable recovery."""
    _emit({
        "command": "inspect",
        "status": "malformed",
        "error_kind": "malformed_cad",
        "format_detected": detection.get("format"),
        "expected_format": detection.get("expected_format"),
        "extension": detection.get("extension"),
        "size_bytes": detection.get("size_bytes"),
        "message": (
            f"{reason} Do not create or repair STEP by writing or truncating "
            "text; use AgentCAD's CAD-kernel-backed run workflow or import a "
            "valid CAD export."
        ),
        "next_actions": [_malformed_recovery_action()],
    }, exit_code=1)


def _inspect_tier0(
    file_path: str,
    detection: dict,
    *,
    with_ids: bool = False,
    with_summary: bool = False,
    id_limit: int | None = DEFAULT_ID_LIMIT,
    validate_only: bool = False,
    validation_timeout: float | None = DEFAULT_VALIDATION_TIMEOUT_S,
) -> None:
    tracker = _ValidationPhaseTracker(
        validation_timeout,
        progress=detection.get("size_bytes", 0) >= LARGE_FILE_PROGRESS_BYTES,
    )
    previous_timeout = _install_validation_timeout(tracker)
    try:
        _inspect_tier0_impl(
            file_path,
            detection,
            with_ids=with_ids,
            with_summary=with_summary,
            id_limit=id_limit,
            validate_only=validate_only,
            tracker=tracker,
        )
    except _InspectValidationTimeout:
        _emit_validation_timeout(
            file_path,
            detection,
            tracker,
            validate_only=validate_only,
        )
    finally:
        _clear_validation_timeout(previous_timeout)


def _emit_validation_timeout(
    file_path: str,
    detection: dict,
    tracker: _ValidationPhaseTracker,
    *,
    validate_only: bool,
) -> None:
    phase = next(
        (
            name
            for name, entry in tracker.entries.items()
            if entry.get("status") == "timeout"
        ),
        None,
    )
    if phase is None:
        phase = tracker.mark_timeout()
    budget_s = tracker.budget_s or 0
    retry_budget = max(180.0, budget_s * 2)
    command_path = shlex.quote(file_path)
    _emit({
        "command": "inspect",
        "status": "validation_timeout",
        "error_kind": "validation_timeout",
        "file": file_path,
        "retryable": True,
        "timed_out_phase": phase,
        "elapsed_s": tracker.elapsed_s,
        "validation_budget_s": budget_s,
        "validation_mode": (
            "structural_only" if validate_only else "deep"
        ),
        "validation_phases": tracker.entries,
        "format_detected": detection.get("format"),
        "extension": detection.get("extension"),
        "size_bytes": detection.get("size_bytes"),
        "message": (
            f"CAD validation exceeded its {budget_s:g}s budget during "
            f"{phase.replace('_', ' ')}. The file was not classified as "
            "malformed."
        ),
        "next_actions": [
            f"agentcad inspect {command_path} --validate-only "
            f"--validation-timeout {retry_budget:g}",
            f"agentcad inspect {command_path} "
            f"--validation-timeout {retry_budget:g}",
        ],
    }, exit_code=1)


def _inspect_tier0_impl(
    file_path: str,
    detection: dict,
    *,
    with_ids: bool,
    with_summary: bool,
    id_limit: int | None,
    validate_only: bool,
    tracker: _ValidationPhaseTracker,
) -> None:
    """Full topology report for STEP/BREP files. OCCT failures are caught and
    surfaced as malformed — never as a stack trace, never as a leaked native
    diagnostic on stdout."""
    try:
        payload, topo_shape = _topology_report(
            file_path,
            return_shape=True,
            format_hint=detection.get("format"),
            validate_only=validate_only,
            tracker=tracker,
        )
    except Exception:
        _emit_malformed_recovery(
            file_path,
            detection,
            (
                f"AgentCAD's CAD kernel could not read {Path(file_path).name} "
                f"as {str(detection.get('format')).upper()} CAD."
            ),
        )
        return

    command_path = shlex.quote(file_path)
    is_versioned = _is_recorded_version_file(Path(file_path))
    payload.update({
        "format_detected": detection.get("format"),
        "extension": detection.get("extension"),
        "size_bytes": detection.get("size_bytes"),
        "validation_mode": (
            "structural_only" if validate_only else "deep"
        ),
        "validation_budget_s": tracker.budget_s or 0,
        "validation_phases": tracker.entries,
    })
    if validate_only:
        tracker.skip("feature_extraction", "Skipped by --validate-only.")
        payload["validation_phases"] = tracker.entries
        payload["next_actions"] = [
            f"agentcad inspect {command_path} "
            f"--validation-timeout {(tracker.budget_s or 0):g}"
        ]
        payload["more_at"] = "agentcad docs inspect"
        _emit(payload, exit_code=0)
        return
    if is_versioned:
        payload.update({
            "next_actions": [
                f"agentcad view {command_path} — open in a browser to inspect "
                "or share with humans",
                f"agentcad measure {command_path} — get dimensions, edge "
                "lengths, and hole diameters",
            ],
            "more_at": "agentcad docs inspect",
        })
    else:
        payload.update({
            "next_actions": [
                f"agentcad import {command_path} — adopt the existing CAD as a "
                "versioned baseline and create edit.py",
                f"agentcad inspect {command_path} --ids — get feature IDs for "
                "targeted edits",
            ],
            "more_at": "agentcad docs editing",
        })

    if with_ids or with_summary:
        with tracker.observe("feature_extraction"):
            from agentcad import topo_ids
            truncations = []
            if with_ids:
                counts = topo_ids.topology_counts(topo_shape)
                payload["solids"] = topo_ids.solid_entries(
                    topo_shape, limit=id_limit
                )
                payload["faces"] = topo_ids.face_entries(
                    topo_shape, limit=id_limit
                )
                payload["edges"] = topo_ids.edge_entries(
                    topo_shape, limit=id_limit
                )
                truncations.extend(_list_truncations(
                    counts,
                    {
                        "solids": len(payload["solids"]),
                        "faces": len(payload["faces"]),
                        "edges": len(payload["edges"]),
                    },
                    id_limit,
                ))
            if with_summary:
                payload["summary"] = topo_ids.summary_entries(
                    topo_shape,
                    id_limit=id_limit,
                )
                truncations.extend(
                    _summary_truncations(payload["summary"])
                )
            if truncations:
                payload["truncation"] = {
                    "limited": True,
                    "limit": id_limit,
                    "fields": truncations,
                    "recommended_commands": _recommended_limit_commands(
                        file_path,
                        with_ids=with_ids,
                        with_summary=with_summary,
                    ),
                }
        # When the agent has IDs (or summary IDs), the most-likely next
        # action shifts: they're here because they want to write an edit
        # script that targets specific features. Point them at the
        # import → load_step → pick_face flow.
        if is_versioned:
            payload["next_actions"] = [
                "agentcad docs editing — use the returned IDs with "
                "pick_face/pick_edge in the existing edit script",
            ]
        else:
            payload["next_actions"] = [
                f"agentcad import {command_path} — adopt the existing CAD as a "
                "versioned baseline and create edit.py",
                "agentcad docs editing — use the returned IDs with "
                "pick_face/pick_edge in edit.py",
            ]
        payload["more_at"] = "agentcad docs editing"
    else:
        tracker.skip(
            "feature_extraction",
            "Not requested; use --ids or --summary for feature details.",
        )

    # A zero-solid input cannot use the normal import/pick/edit flow, even when
    # OCCT considers its surface topology valid. Give the categorical guidance
    # precedence over edit-risk guidance, which is about difficult solids.
    if payload.get("solid_count") == 0:
        rebuild_action = (
            "follow recommended_workflow — rebuild a solid from usable profiles "
            "or faces instead of attempting normal solid edits"
            if payload.get("edit_risk") == "high"
            else "agentcad docs helpers — rebuild a solid with an extrusion, "
            "loft_sections, or another parametric construction"
        )
        payload["next_actions"] = [
            f"agentcad view {file_path} — inspect which surfaces or profiles are usable",
            rebuild_action,
        ]

    # On a high edit-risk input the generic next_actions above (view, measure,
    # or the import → pick_face flow) all assume the part is normally editable,
    # which contradicts the recommended_workflow. Make the next step coherent:
    # look, then follow the workflow — don't jump straight into an edit flow.
    elif payload.get("edit_risk") == "high":
        payload["next_actions"] = [
            f"agentcad view {file_path} — look at the geometry before deciding how to edit",
            "follow recommended_workflow — do not start with load_step() + "
            "boolean/fillet edits on this input",
        ]

    notes = _compute_notes(payload)
    if with_ids or with_summary:
        # ID-stability caveat — load-bearing for the addressability flow.
        # Per the `notes` convention, fires whenever IDs are emitted (full
        # via --ids or per-cluster via --summary) because the same caveat
        # always applies.
        notes.append(
            "Feature IDs are 1-indexed and stable within this shape (OCP "
            "topology-traversal order) but not across edits — even a small "
            "boolean or fillet can renumber faces and edges. Re-run "
            "`inspect --ids` (or `--summary`) after each edit to get current IDs."
        )
    if notes:
        payload["notes"] = notes

    payload["validation_phases"] = tracker.entries

    _emit(payload, exit_code=0)


def _is_recorded_version_file(file_path: Path) -> bool:
    """Whether ``file_path`` belongs to a version recorded by this project.

    Manifest history records a version directory rather than every artifact.
    Treating files beneath that directory as versioned covers normalized STEP
    outputs and imported source copies without depending on optional metadata.
    A missing or malformed manifest must not break ``inspect``'s never-throws
    contract, so uncertain cases remain eligible for import guidance.
    """
    from agentcad.manifest import MANIFEST_FILE

    manifest_path = Path.cwd() / MANIFEST_FILE
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text())
        if not isinstance(manifest, dict):
            return False
        target = file_path.resolve()
        for version in manifest.get("versions", []):
            if not isinstance(version, dict):
                continue
            recorded_path = version.get("path")
            if not isinstance(recorded_path, str) or not recorded_path:
                continue
            version_dir = (Path.cwd() / recorded_path).resolve()
            if target == version_dir or version_dir in target.parents:
                return True
    except (OSError, ValueError, TypeError, RuntimeError):
        return False
    return False


def _list_truncations(
    totals: dict,
    returned: dict,
    limit: int | None,
) -> list[dict]:
    if limit is None:
        return []
    fields = []
    for name, total in totals.items():
        count = returned[name]
        if count < total:
            fields.append({
                "path": name,
                "returned": count,
                "total": total,
                "omitted": total - count,
            })
    return fields


def _summary_truncations(summary: dict) -> list[dict]:
    id_truncation = summary.get("id_truncation")
    if not id_truncation:
        return []
    fields = []
    for field in id_truncation["fields"]:
        fields.append({
            "path": f"summary.{field['path']}.ids",
            "limit_per_cluster": field["limit_per_cluster"],
            "truncated_clusters": field["truncated_clusters"],
            "omitted": field["omitted_ids"],
        })
    return fields


def _recommended_limit_commands(
    file_path: str,
    *,
    with_ids: bool,
    with_summary: bool,
) -> list[str]:
    commands = []
    if with_summary:
        commands.extend([
            f"agentcad inspect {file_path} --summary --limit 500",
            f"agentcad inspect {file_path} --summary --no-limit",
        ])
    if with_ids:
        commands.extend([
            f"agentcad inspect {file_path} --ids --limit 500",
            f"agentcad inspect {file_path} --ids --no-limit",
        ])
    if not with_summary:
        commands.insert(0, f"agentcad inspect {file_path} --summary")
    return commands


def _compute_notes(payload: dict) -> list:
    """Contextual clarifications that fire only when a combination of fields
    might confuse an agent without domain knowledge. See `notes` convention
    in `design_conventions.md`."""
    notes = []
    if payload.get("solid_count") == 0:
        from agentcad.geometry_notes import NO_SOLID_BODY_NOTE
        notes.append(NO_SOLID_BODY_NOTE)

    # is_valid==True together with free_edge_count > 0 reads as contradictory
    # to an unfamiliar agent ("the shape has dangling edges *and* the tool
    # says it's valid"). Both can be correct: is_valid checks topological
    # consistency, not closure. Free edges may appear on intentionally open
    # shells (sheet metal, surfaces) or as artifacts of boolean/fillet ops
    # on closed solids — the note covers both cases without claiming either.
    # Asymmetric face_orientations (forward vs reversed) on a valid closed
    # solid looks alarming but isn't a defect — OCCT's importer happens to
    # orient faces however it likes during STEP read, and the resulting split
    # can be wildly skewed (e.g. forward=11, reversed=107 on the Pump Manifold
    # e2e test). is_valid + free_edge_count=0 confirm the topology is sound.
    # Add a note when the asymmetry shows up so agents don't chase a non-bug.
    orientations = payload.get("face_orientations", {})
    fwd = orientations.get("forward", 0)
    rev = orientations.get("reversed", 0)
    if (
        payload.get("is_valid")
        and payload.get("free_edge_count", 0) == 0
        and fwd != rev
        and (fwd + rev) > 0
    ):
        notes.append(
            "face_orientations asymmetry (forward vs reversed counts differ) on "
            "a closed valid solid is normal — it reflects how OCCT oriented "
            "faces during import, not a defect. is_valid: true with "
            "free_edge_count: 0 confirms the topology is sound."
        )

    if payload.get("is_valid") and payload.get("free_edge_count", 0) > 0:
        notes.append(
            "is_valid: true means every shell is closed and the surface "
            "meshes as a closed manifold, so free_edge_count > 0 here counts "
            "seam edges or shared-edge bookkeeping, not holes."
        )
    if payload.get("is_valid") and (payload.get("solid_count") or 0) > 1:
        notes.append(
            f"{payload['solid_count']} separate closed solids: deliverable, but "
            "not one connected body. Bodies that only touch along an edge or "
            "at a corner stay separate after a fuse; overlap them if you want "
            "one solid."
        )
    validation = payload.get("validation") or {}
    if payload.get("is_valid") is False and validation.get("first_failure"):
        notes.append(
            f"is_valid: false because the {validation['first_failure']} layer "
            f"failed: {validation.get('message')}"
        )
    if payload.get("is_valid") is None:
        notes.append(
            "is_valid: null means a validation layer could not finish inside "
            "its budget; see validation.undetermined_layer. The shape was not "
            "classified as invalid."
        )
    return notes


def _topology_report(
    file_path: str,
    *,
    return_shape: bool = False,
    format_hint: str | None = None,
    validate_only: bool = False,
    tracker: _ValidationPhaseTracker,
):
    from agentcad.step_io import load_cad_shape
    from agentcad.validation import validate_shape

    with tracker.observe("native_load"):
        # Raw OCCT parser output is replaced by the structured error contract.
        with suppress_native_output():
            shape = load_cad_shape(file_path, format_hint=format_hint)

    with tracker.observe("structural_validation"):
        # M71: is_valid is the full deliverable verdict (kernel check, shell
        # closure, manifold mesh), not the kernel check alone. The mesh layer
        # runs in its own bounded worker; the kernel-only result stays
        # available as validation.layers.brep_check.
        report = validate_shape(shape)
        # Loading happened in native_load; reflect its cost in the layer report
        # so the two views of the same work agree.
        native = tracker.entries.get("native_load") or {}
        if native.get("duration_ms") is not None:
            report["layers"]["kernel_load"]["duration_ms"] = native["duration_ms"]
        is_valid = report["is_valid"]
        validity_errors = list(report["layers"]["brep_check"].get("errors") or [])

    payload = {
        "command": "inspect",
        "status": "success",
        "file": file_path,
        "is_valid": is_valid,
        "validation": report,
    }
    if validity_errors:
        payload["validity_errors"] = validity_errors

    if validate_only:
        tracker.skip("topology_extraction", "Skipped by --validate-only.")
        if return_shape:
            return payload, shape
        return payload

    with tracker.observe("topology_extraction"):
        from OCP.ShapeAnalysis import ShapeAnalysis_Shell
        from OCP.TopAbs import (
            TopAbs_EDGE, TopAbs_FACE, TopAbs_FORWARD, TopAbs_REVERSED,
            TopAbs_SHELL, TopAbs_SOLID,
        )
        from OCP.TopExp import TopExp, TopExp_Explorer
        from OCP.TopoDS import TopoDS
        from OCP.TopTools import (
            TopTools_IndexedDataMapOfShapeListOfShape,
            TopTools_IndexedMapOfShape,
        )

        with suppress_native_output():
            solid_count = 0
            exp = TopExp_Explorer(shape, TopAbs_SOLID)
            while exp.More():
                solid_count += 1
                exp.Next()

            shells = []
            exp = TopExp_Explorer(shape, TopAbs_SHELL)
            while exp.More():
                shell = TopoDS.Shell_s(exp.Current())
                sa = ShapeAnalysis_Shell()
                sa.LoadShells(shell)
                sa.CheckOrientedShells(shell, True)
                has_free = sa.HasFreeEdges()
                shell_face_count = 0
                face_exp = TopExp_Explorer(shell, TopAbs_FACE)
                while face_exp.More():
                    shell_face_count += 1
                    face_exp.Next()
                shells.append({
                    "closed": not has_free,
                    "face_count": shell_face_count,
                })
                exp.Next()

            face_map = TopTools_IndexedMapOfShape()
            TopExp.MapShapes_s(shape, TopAbs_FACE, face_map)
            face_count = face_map.Extent()

            forward = reversed_count = 0
            exp = TopExp_Explorer(shape, TopAbs_FACE)
            while exp.More():
                orient = exp.Current().Orientation()
                if orient == TopAbs_FORWARD:
                    forward += 1
                elif orient == TopAbs_REVERSED:
                    reversed_count += 1
                exp.Next()

            edge_map = TopTools_IndexedMapOfShape()
            TopExp.MapShapes_s(shape, TopAbs_EDGE, edge_map)
            edge_count = edge_map.Extent()

            # Build edge→face ancestry once. The previous nested traversal
            # rescanned every face and its edges for every edge (effectively
            # cubic on large compounds), which dominated fixture-202 inspect.
            edge_faces = TopTools_IndexedDataMapOfShapeListOfShape()
            TopExp.MapShapesAndAncestors_s(
                shape,
                TopAbs_EDGE,
                TopAbs_FACE,
                edge_faces,
            )
            free_edge_count = 0
            for i in range(1, edge_count + 1):
                edge = edge_map.FindKey(i)
                if (
                    not edge_faces.Contains(edge)
                    or edge_faces.FindFromKey(edge).Extent() < 2
                ):
                    free_edge_count += 1

        payload.update({
            "solid_count": solid_count,
            "shell_count": len(shells),
            "shells": shells,
            "face_count": face_count,
            "face_orientations": {
                "forward": forward,
                "reversed": reversed_count,
            },
            "edge_count": edge_count,
            "free_edge_count": free_edge_count,
        })

        from agentcad.edit_risk import classify_edit_risk
        risk = classify_edit_risk(
            face_count=face_count,
            edge_count=edge_count,
            is_valid=is_valid,
            free_edge_count=free_edge_count,
            validity_errors=validity_errors,
        )
        if risk:
            payload.update(risk)
    if return_shape:
        return payload, shape
    return payload
