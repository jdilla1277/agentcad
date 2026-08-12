import ast
import json
import os
import re
import signal
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import click

from agentcad.commands._daemon_routing import (
    maybe_route_through_daemon,
    maybe_spawn_daemon_for_next_run,
)
from agentcad.commands.export_cmd import (
    NO_FORMATS_MESSAGE,
    parse_export_formats,
    unsupported_export_formats,
)
from agentcad.manifest import MANIFEST_FILE, load_manifest, save_manifest


_DEFAULT_RUN_TIMEOUT_S = 115.0
_RUN_TIMEOUT_ENV = "AGENTCAD_RUN_TIMEOUT_S"
_ACTIVE_PHASE_TRACKER = None
_PREVIOUS_ALARM_HANDLER = None
_RUN_TIMEOUT_INSTALLED = False


class _RunTimeout(BaseException):
    """Raised by the SIGALRM watchdog so broad script catches don't swallow it."""

    def __init__(self, payload):
        super().__init__(payload.get("message", "agentcad run timed out"))
        self.payload = payload


class _PhaseTracker:
    def __init__(self, timings):
        self.timings = timings
        self.completed = []
        self.active_phase = None
        self.active_started_at = None
        self.next_phase = None

    def start(self, phase):
        self.active_phase = phase
        self.next_phase = phase
        self.active_started_at = time.perf_counter()

    def complete(self, phase):
        if phase not in self.completed:
            self.completed.append(phase)
        if self.active_phase == phase:
            self.active_phase = None
            self.active_started_at = None
        self.next_phase = None

    def timeout_payload(self, timeout_s):
        phase = self.active_phase or self.next_phase or "unknown"
        payload = {
            "command": "run",
            "status": "failed",
            "error_kind": "timeout",
            "timeout_s": timeout_s,
            "timeout_phase": phase,
            "completed_phases": list(self.completed),
            "phase_timings": dict(self.timings),
            "timings": dict(self.timings),
            "message": (
                f"agentcad run timed out after {timeout_s:g}s"
                f"{f' during {phase}' if phase != 'unknown' else ''}"
            ),
            "suggestion": _timeout_suggestion(phase, self.completed),
        }
        if self.active_started_at is not None:
            payload["active_phase_elapsed_ms"] = round(
                (time.perf_counter() - self.active_started_at) * 1000
            )
        if self.completed:
            payload["last_completed_phase"] = self.completed[-1]
        return payload


def _timeout_suggestion(phase, completed):
    suggestions = {
        "validation": (
            "Static validation or runtime detection timed out; check for very large "
            "source files or slow imports during runtime selection."
        ),
        "script_exec": (
            "The script timed out before producing exportable geometry; simplify "
            "the algorithm, avoid expensive search loops, or avoid slow STEP import "
            "inside the script."
        ),
        "metrics": (
            "The script executed but geometry metrics timed out; inspect whether "
            "the resulting shape is extremely complex or invalid."
        ),
        "export_step": (
            "The script executed but STEP export timed out; avoid wrapping a large "
            "or invalid imported STEP as a high-level part, or export a simpler "
            "compound/assembly."
        ),
        "export_mesh": (
            "Mesh export timed out; try skipping optional mesh exports or reducing "
            "geometry complexity."
        ),
        "render_views": (
            "Requested view rendering timed out; rerun without --render while "
            "iterating on geometry."
        ),
        "preview": (
            "Preview rendering timed out; rerun with --no-preview if metrics and "
            "STEP output are enough for this iteration."
        ),
        "parts_preview": (
            "Per-part preview rendering timed out; rerun with --no-preview or "
            "reduce the number/complexity of shown parts."
        ),
        "export_viewer_glb": (
            "Viewer GLB export timed out; inspect shape validity and consider "
            "simplifying the model before generating viewer assets."
        ),
        "diff": (
            "Diff rendering against the previous version timed out; continue from "
            "the latest STEP and skip visual comparison for this iteration."
        ),
        "viewer": (
            "The geometry exported, but viewer generation timed out; use the STEP "
            "and metrics artifacts directly for this iteration."
        ),
    }
    if phase in suggestions:
        return suggestions[phase]
    if completed:
        return (
            f"Timeout happened after {completed[-1]}; inspect the next artifact "
            "phase or rerun with optional previews/renders disabled."
        )
    return "Timeout phase is unknown; rerun with --no-preview and fewer optional exports."


def _run_timeout_seconds():
    raw = os.environ.get(_RUN_TIMEOUT_ENV)
    if raw in (None, ""):
        return _DEFAULT_RUN_TIMEOUT_S
    try:
        timeout_s = float(raw)
    except ValueError:
        return _DEFAULT_RUN_TIMEOUT_S
    if timeout_s <= 0:
        return None
    return timeout_s


def _handle_run_timeout(_signum, _frame):
    tracker = _ACTIVE_PHASE_TRACKER
    timeout_s = _run_timeout_seconds() or 0
    payload = (
        tracker.timeout_payload(timeout_s)
        if tracker is not None
        else {
            "command": "run",
            "status": "failed",
            "error_kind": "timeout",
            "timeout_s": timeout_s,
            "timeout_phase": "unknown",
            "completed_phases": [],
            "phase_timings": {},
            "timings": {},
            "message": f"agentcad run timed out after {timeout_s:g}s",
            "suggestion": _timeout_suggestion("unknown", []),
        }
    )
    raise _RunTimeout(payload)


def _install_run_timeout(phase_tracker):
    global _ACTIVE_PHASE_TRACKER, _PREVIOUS_ALARM_HANDLER, _RUN_TIMEOUT_INSTALLED
    timeout_s = _run_timeout_seconds()
    if timeout_s is None:
        return
    if threading.current_thread() is not threading.main_thread():
        return
    if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
        return
    _ACTIVE_PHASE_TRACKER = phase_tracker
    _PREVIOUS_ALARM_HANDLER = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _handle_run_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_s)
    _RUN_TIMEOUT_INSTALLED = True


def _clear_run_timeout():
    global _ACTIVE_PHASE_TRACKER, _PREVIOUS_ALARM_HANDLER, _RUN_TIMEOUT_INSTALLED
    if (
        _RUN_TIMEOUT_INSTALLED
        and threading.current_thread() is threading.main_thread()
        and hasattr(signal, "SIGALRM")
        and hasattr(signal, "setitimer")
    ):
        signal.setitimer(signal.ITIMER_REAL, 0)
        if _PREVIOUS_ALARM_HANDLER is not None:
            signal.signal(signal.SIGALRM, _PREVIOUS_ALARM_HANDLER)
    _ACTIVE_PHASE_TRACKER = None
    _PREVIOUS_ALARM_HANDLER = None
    _RUN_TIMEOUT_INSTALLED = False


def _parse_params(raw):
    """Parse a --params string like 'length=60,width=20' into a dict."""
    params = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" not in pair:
            raise ValueError(f"Invalid param format: '{pair}'. Expected key=value.")
        key, val = pair.split("=", 1)
        key, val = key.strip(), val.strip()
        # Bool
        if val.lower() in ("true", "false"):
            params[key] = val.lower() == "true"
        else:
            # Int → Float → String
            try:
                params[key] = int(val)
            except ValueError:
                try:
                    params[key] = float(val)
                except ValueError:
                    params[key] = val
    return params


def _enrich_error(msg):
    """Add actionable context to common OCC error messages."""
    if "BRep_API: command not done" in msg:
        msg += (
            " — this usually means a wire is not closed. "
            "Check that all edge endpoints connect within tolerance (~1e-7mm). "
            "Common cause: floating-point drift across many rotated/translated points."
        )
    return msg


def _uncalled_part_topology_methods(source):
    """Return Part collection attributes referenced without being called.

    The runtime ``TypeError: 'method' object is not iterable`` is generic, so
    only attach Part-specific guidance when the source itself contains one of
    the imported-Part collection methods as a bare attribute.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    called_attributes = {
        id(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    return sorted({
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in {"solids", "faces", "edges"}
        and id(node) not in called_attributes
    })


def _execution_error_guidance(msg, runtime, source):
    """Return focused recovery fields for known build123d Part mistakes."""
    if runtime != "build123d":
        return {}

    suggestion = None
    if "'Part' object has no attribute 'BoundingBox'" in msg:
        suggestion = (
            "`load_step()` returns a build123d `Part`. Get its bounds with "
            "`base.bounding_box()`."
        )
    elif "'Part' object has no attribute 'IsNull'" in msg:
        suggestion = (
            "`load_step()` returns a build123d `Part`. Use `agentcad inspect` "
            "or run metrics for validity; use `load_step_shape()` only when "
            "the edit requires a raw topology shape."
        )
    elif "'method' object is not iterable" in msg:
        methods = _uncalled_part_topology_methods(source)
        if methods:
            calls = ", ".join(f"`base.{method}()`" for method in methods)
            names = ", ".join(f"`{method}`" for method in methods)
            suggestion = (
                f"{names} {'is a method' if len(methods) == 1 else 'are methods'} "
                f"on the Part returned by `load_step()`. Call "
                f"{'it' if len(methods) == 1 else 'them'} as {calls}."
            )

    if suggestion is None:
        return {}
    return {
        "suggestion": suggestion,
        "more_at": "agentcad docs editing",
    }


def _find_prev_success(versions):
    """Return the most recent successful version in the manifest, or None.

    Skips failed runs. Used to pick the baseline for auto-diff on every run.
    """
    for entry in reversed(versions):
        if entry.get("status") == "success":
            return entry
    return None


def _normalized_part_value(value):
    """Normalize nested metric values so harmless float noise is ignored."""
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {key: _normalized_part_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalized_part_value(item) for item in value]
    return value


def _part_change_summary(previous_parts, current_parts, previous_label):
    """Build the compact stable-ID part delta embedded in viewer.html."""
    previous_by_id = {
        str(part["id"]): part
        for part in (previous_parts or [])
        if part.get("id") is not None
    }
    current_by_id = {
        str(part["id"]): part
        for part in (current_parts or [])
        if part.get("id") is not None
    }

    def snapshot(part):
        return {
            "id": str(part["id"]),
            **({"name": part["name"]} if part.get("name") else {}),
        }

    added = [
        snapshot(current_by_id[part_id])
        for part_id in sorted(current_by_id.keys() - previous_by_id.keys())
    ]
    removed = [
        snapshot(previous_by_id[part_id])
        for part_id in sorted(previous_by_id.keys() - current_by_id.keys())
    ]
    renamed = []
    changed = []
    for part_id in sorted(previous_by_id.keys() & current_by_id.keys()):
        before = previous_by_id[part_id]
        after = current_by_id[part_id]
        if before.get("name") != after.get("name"):
            renamed.append({
                "id": part_id,
                "from": before.get("name") or part_id,
                "to": after.get("name") or part_id,
            })

        fields = []
        if _normalized_part_value(before.get("metrics")) != _normalized_part_value(
            after.get("metrics")
        ):
            fields.append("geometry")
        if before.get("part_of") != after.get("part_of"):
            fields.append("group")
        if before.get("color") != after.get("color"):
            fields.append("color")
        if fields:
            changed.append({
                "id": part_id,
                "name": after.get("name") or part_id,
                "fields": fields,
            })

    counts = [
        (len(added), "added"),
        (len(removed), "removed"),
        (len(renamed), "renamed"),
        (len(changed), "changed"),
    ]
    changed_count = sum(count for count, _label in counts)
    summary = (
        "No named part changes"
        if changed_count == 0
        else " · ".join(f"{count} {label}" for count, label in counts if count)
    )
    return {
        "against": previous_label,
        "summary": summary,
        "added": added,
        "removed": removed,
        "renamed": renamed,
        "changed": changed,
    }


def _record_failure(
    manifest,
    script_path,
    label,
    version_num,
    error_msg,
    runtime=None,
    guidance=None,
):
    """Record a script failure on disk and in the manifest."""
    dir_name = f"v{version_num}_{label}_failed"
    version_dir = Path.cwd() / dir_name
    version_dir.mkdir(parents=True, exist_ok=True)

    # Copy script into failed directory
    shutil.copy2(str(script_path), str(version_dir / "script.py"))

    # Write meta.json
    created = datetime.now(timezone.utc).isoformat()
    meta = {
        "version": version_num,
        "label": label,
        "status": "failed",
        "created": created,
        "error": error_msg,
        "script": f"{dir_name}/script.py",
    }
    if runtime is not None:
        meta["runtime"] = runtime
    if guidance:
        meta.update(guidance)
    (version_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    # Update manifest (current does NOT advance)
    versions = manifest.get("versions", [])
    versions.append({
        "version": version_num,
        "label": label,
        "status": "failed",
        "path": f"{dir_name}/",
    })
    manifest["versions"] = versions
    save_manifest(manifest)

    # Output failure JSON
    output_json = {
        "command": "run",
        "status": "failed",
        "version": version_num,
        "label": label,
        "error": error_msg,
        "path": f"{dir_name}/",
    }
    if runtime is not None:
        output_json["runtime"] = runtime
    if guidance:
        output_json.update(guidance)
    click.echo(json.dumps(output_json))
    sys.exit(1)


def _record_invalid_geometry(
    manifest,
    script_path,
    label,
    version_num,
    runtime,
    output_type,
    metrics,
    parts,
    groups,
    warnings,
    response,
):
    """Preserve invalid-geometry diagnostics without advancing current."""
    dir_name = f"v{version_num}_{label}_invalid"
    version_dir = Path.cwd() / dir_name
    version_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(script_path), str(version_dir / "script.py"))

    meta = {
        **response,
        "version_recorded": True,
        "version": version_num,
        "label": label,
        "runtime": runtime,
        "output_type": output_type,
        "created": datetime.now(timezone.utc).isoformat(),
        "script": f"{dir_name}/script.py",
    }
    if parts:
        meta["parts"] = parts
    if groups:
        meta["groups"] = groups
    if warnings:
        meta["warnings"] = warnings
    (version_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    versions = manifest.get("versions", [])
    versions.append({
        "version": version_num,
        "label": label,
        "status": "invalid_geometry",
        "path": f"{dir_name}/",
    })
    manifest["versions"] = versions
    save_manifest(manifest)

    output_json = {
        **response,
        "version_recorded": True,
        "version": version_num,
        "label": label,
        "runtime": runtime,
        "output_type": output_type,
        "path": f"{dir_name}/",
    }
    if parts:
        output_json["parts"] = parts
    if groups:
        output_json["groups"] = groups
    if warnings:
        output_json["warnings"] = warnings
    click.echo(json.dumps(output_json))
    sys.exit(1)


def _slugify_part_id(value):
    """Return a lowercase, JSON/path-friendly part handle."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or None


def _dedupe_part_id(base, used):
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _resolve_group_id(value):
    if value is None:
        return None, None
    group_name = str(value).strip()
    if not group_name:
        return None, None
    return _slugify_part_id(group_name), group_name


def _assign_part_identity(raw_parts):
    """Resolve public part IDs from author intent, name, then ordinal fallback."""
    used: set[str] = set()
    resolved = []
    for idx, p in enumerate(raw_parts):
        explicit = p.get("explicit_id")
        if explicit is not None:
            base = _slugify_part_id(explicit)
            if base is None:
                base = f"part_{idx}"
            part_id = _dedupe_part_id(base, used)
            source = "explicit"
        elif p.get("name"):
            base = _slugify_part_id(p["name"])
            if base is None:
                base = f"part_{idx}"
                source = "generated"
            else:
                source = "name"
            part_id = _dedupe_part_id(base, used)
        else:
            part_id = _dedupe_part_id(f"part_{idx}", used)
            source = "generated"
        resolved.append((part_id, source))
    return resolved




@click.command()
@click.argument("script")
@click.option("--output", required=True, help="Label for this version.")
@click.option(
    "--render",
    default=None,
    help=(
        "Comma-separated named views, 'all' (front,right,top,iso), custom "
        "azimuth:elevation angles, or a mix such as front,45:30."
    ),
)
@click.option("--export", default=None, help="Comma-separated mesh formats to export (stl, glb, obj).")
@click.option(
    "--preview/--no-preview",
    default=True,
    help=(
        "4-view composite PNG + per-part previews (default on, ~2-4s). "
        "The viewer.html, GLB, and diff PNGs always generate regardless — "
        "--no-preview skips the composite and per-part previews. Use it when "
        "you don't need agent-readable PNGs this iteration."
    ),
)
@click.option("--view/--no-view", "open_view", default=True, help="Open the generated review viewer after a successful run (default on). From v2 onward it preloads previous/current A/B comparison.")
@click.option("--params", default=None, help="Parameter overrides as key=value,key=value.")
@click.option("--dry-run", is_flag=True, default=False, help="Compute metrics without creating a version or disk artifacts.")
@click.option(
    "--runtime",
    default=None,
    type=click.Choice(["cadquery", "build123d"]),
    help=(
        "One-off runtime override. Without it, the project runtime is "
        "authoritative; legacy unpinned projects detect source syntax, then "
        "fall back to build123d."
    ),
)
@click.option("--no-daemon", is_flag=True, default=False, help="Skip daemon routing for this run, even if a daemon is running. Useful for debugging.")
@click.pass_context
def run(ctx, script, output, render, export, preview, open_view, params, dry_run, runtime, no_daemon):
    """Execute the project's CAD script and produce a versioned STEP file.

    New projects use build123d. CadQuery scripts remain supported in projects
    explicitly pinned to the compatibility runtime or through
    ``--runtime cadquery``.

    If `script` is a CAD file (.step / .stp / .brep), dispatches to
    `agentcad import` instead — agents who instinctively reach for `run`
    when handed a CAD file get the right behavior automatically.
    """
    # Reject unsupported --export formats before anything else — before the
    # CAD-file suffix dispatch below (which drops --export entirely), daemon
    # routing, version allocation, or any disk artifacts — so `run --export`
    # matches `agentcad export` instead of silently ignoring unknown formats.
    if export is not None:
        # An all-blank value (e.g. `--export ","`) parses to an empty list.
        # Without this the run would execute the whole script and consume a
        # version number, then export nothing and still report success.
        if not parse_export_formats(export):
            click.echo(json.dumps({
                "command": "run",
                "status": "error",
                "message": NO_FORMATS_MESSAGE,
            }))
            sys.exit(1)
        invalid = unsupported_export_formats(export)
        if invalid:
            click.echo(json.dumps({
                "command": "run",
                "status": "error",
                "message": (
                    f"Unsupported format(s): {', '.join(invalid)}. "
                    f"Supported: stl, glb, obj"
                ),
            }))
            sys.exit(1)

    # M60 Phase 2 (slice 2b): suffix-dispatch CAD files to `agentcad import`.
    # This closes a footgun where agents handed a STEP would write
    # `agentcad run widget.step` and hit a confusing parse error. Tier 0
    # files actually import; Tier 1+ files surface the polite-no responses
    # via the same dispatch (better than a Python parse error).
    from agentcad import file_detect as _fd
    if _fd.is_recognized_cad_extension(script):
        from agentcad.commands.import_cmd import import_cmd
        ctx.invoke(
            import_cmd,
            file=script,
            label=output,
            init_flag=False,
            open_view=open_view,
            no_daemon=no_daemon,
        )
        return

    # Run the actual work inside a try/except so internal exceptions
    # (e.g. `Bnd_Box is void` from compute_metrics on a degenerate
    # shape) surface as JSON on stdout — preserving the docs' "All
    # output is JSON" contract. Without this, daemon-routed runs emit
    # only stderr heartbeats and exit non-zero with empty stdout, and
    # the agent has no recovery signal.
    try:
        _run_impl(
            ctx, script, output, render, export,
            preview, open_view, params, dry_run, runtime, no_daemon,
        )
    except SystemExit:
        # Explicit sys.exit() calls inside _run_impl are intentional —
        # they already emitted the proper JSON. Pass through.
        raise
    except _RunTimeout as e:
        click.echo(json.dumps(e.payload))
        sys.exit(1)
    except Exception as e:
        import traceback as _tb
        click.echo(json.dumps({
            "command": "run",
            "status": "error",
            "message": f"Internal error: {type(e).__name__}: {e}",
            "traceback": _tb.format_exc(),
        }))
        sys.exit(1)
    finally:
        _clear_run_timeout()


def _run_impl(ctx, script, output, render, export, preview, open_view, params,
              dry_run, runtime, no_daemon):
    _t_total_start = time.perf_counter()
    _timings = {}
    _phase_tracker = _PhaseTracker(_timings)
    _install_run_timeout(_phase_tracker)

    def _mark(key, start):
        _timings[key] = round((time.perf_counter() - start) * 1000)

    def _start_phase(phase):
        _phase_tracker.start(phase)
        return time.perf_counter()

    def _finish_phase(phase, start, key=None):
        _mark(key or f"{phase}_ms", start)
        _phase_tracker.complete(phase)

    def _heartbeat(message):
        # Stderr progress line so callers can distinguish "still working"
        # from "wedged" during long phases (preview render, GIF encode).
        # One line per phase, always-on, lightweight. Stays on stderr so
        # the JSON on stdout remains parseable. Issue #164.
        click.echo(f"[agentcad] {message}", err=True)


    # Try routing through daemon. If reachable, this exits before returning.
    argv = ["run", script, "--output", output]
    if render:
        argv.extend(["--render", render])
    if export:
        argv.extend(["--export", export])
    if not preview:
        argv.append("--no-preview")
    if not open_view:
        argv.append("--no-view")
    if params:
        argv.extend(["--params", params])
    if dry_run:
        argv.append("--dry-run")
    if runtime:
        argv.extend(["--runtime", runtime])
    maybe_route_through_daemon(argv, no_daemon=no_daemon)

    # Fallback: direct execution
    _t = _start_phase("validation")
    manifest = load_manifest(command="run")

    # Python version check (before CadQuery imports)
    if sys.version_info >= (3, 13):
        click.echo(json.dumps({
            "command": "run",
            "status": "error",
            "message": (
                f"agentcad requires Python 3.10-3.12 "
                f"(found {sys.version_info[0]}.{sys.version_info[1]}). "
                f"CadQuery/OCP bindings are not available on newer Python versions."
            ),
        }))
        sys.exit(1)

    script_path = Path(script)
    if not script_path.exists():
        click.echo(json.dumps({
            "command": "run",
            "status": "error",
            "message": f"Script file '{script}' not found",
        }))
        sys.exit(1)

    # Dispatch to the right runner. Precedence:
    #   --runtime flag > project mode > legacy source detection > default
    from agentcad.runners import dispatch

    raw_source = script_path.read_text()
    project_default = dispatch.project_runtime()

    try:
        runtime_name, runner = dispatch.resolve(
            raw_source, override=runtime, project_default=project_default
        )
    except ValueError as e:
        # Ambiguous/mismatched source or unknown --runtime — surface cleanly.
        click.echo(json.dumps({
            "command": "run",
            "status": "error",
            "message": str(e),
        }))
        sys.exit(1)

    # Pre-execution validation (before version allocation)
    validation_errors = runner.validate(raw_source)
    if validation_errors:
        click.echo(json.dumps({
            "command": "run",
            "status": "validation_error",
            "runtime": runtime_name,
            "checks": validation_errors,
        }))
        sys.exit(1)

    # Parse --params before version allocation (errors should be cheap)
    parsed_params = None
    if params:
        try:
            parsed_params = _parse_params(params)
        except ValueError as e:
            click.echo(json.dumps({
                "command": "run",
                "status": "error",
                "message": str(e),
            }))
            sys.exit(1)

    # Validate --render spec before version allocation (errors should be cheap)
    if render:
        from agentcad.render import parse_view_spec as _parse_view_spec
        try:
            _parse_view_spec(render)
        except ValueError as e:
            click.echo(json.dumps({
                "command": "run",
                "status": "error",
                "message": str(e),
            }))
            sys.exit(1)
    _finish_phase("validation", _t, "validation_ms")

    # Spawn the daemon NOW — after the runner module is imported (so OCP is
    # in memory) but BEFORE the script executes. The script may use boolean
    # operations (cut/fuse/intersect) which initialize OCP's TBB thread
    # pool; fork-after-TBB-init creates a child with TBB state but no
    # actual worker threads, and the daemon would deadlock on its first
    # parallel-OCP request.
    maybe_spawn_daemon_for_next_run(no_daemon=no_daemon)

    # Execute via the runner — returns a runtime-agnostic ExecutionResult.
    _heartbeat(f"running script ({runtime_name})…")
    _t = _start_phase("script_exec")
    result = runner.execute(raw_source, parsed_params)

    # Param validation errors (unknown names, CQGI InvalidParameterError) —
    # surface them without consuming a version number.
    if result.status == "validation_error":
        click.echo(json.dumps({
            "command": "run",
            "status": "error",
            "runtime": runtime_name,
            "message": result.exception,
        }))
        sys.exit(1)

    # Determine version number before recording failures (failures consume a number)
    versions = manifest.get("versions", [])
    version_num = len(versions) + 1
    label = output

    if result.status == "execution_error":
        error_msg = _enrich_error(result.exception)
        guidance = _execution_error_guidance(
            error_msg,
            runtime=runtime_name,
            source=raw_source,
        )
        _record_failure(
            manifest,
            script_path,
            label,
            version_num,
            error_msg,
            runtime=runtime_name,
            guidance=guidance,
        )

    # Success path
    shape = result.native_shape
    warnings = list(result.warnings)

    _finish_phase("script_exec", _t, "script_exec_ms")

    # Compute geometric metrics
    _heartbeat("computing metrics…")
    _t = _start_phase("metrics")
    from agentcad.metrics import compute_metrics

    topo_shape_for_metrics = result.topo_shape
    metrics = compute_metrics(topo_shape_for_metrics)

    # Per-part breakdown (feedback #190): one entry per show_object() call.
    # Metrics computed now so --dry-run also surfaces them; per-part previews
    # are rendered later (below) only when the version directory is being
    # materialized and --no-preview wasn't passed.
    parts_output: list[dict] = []
    groups_by_id: dict[str, dict] = {}
    raw_parts = result.parts
    output_type = result.output_type or (
        "assembly" if len(raw_parts) > 1 else "single_part"
    )
    part_id_sources = _assign_part_identity(raw_parts)
    part_groups: list[tuple[str | None, str | None]] = []
    for p, (part_id, _id_source) in zip(raw_parts, part_id_sources):
        group_id, group_name = _resolve_group_id(p.get("part_of"))
        part_groups.append((group_id, group_name))
        group_color = p.get("group_color")
        if group_id is None:
            if group_color is not None:
                warnings.append(
                    f"group_color ignored for ungrouped part {part_id!r}; "
                    "set options.part_of to create a group."
                )
            continue

        group = groups_by_id.setdefault(
            group_id,
            {"id": group_id, "name": group_name, "part_ids": []},
        )
        group["part_ids"].append(part_id)
        if group_color is None:
            continue
        existing_color = group.get("color")
        if existing_color is None:
            group["color"] = group_color
        elif existing_color != group_color:
            warnings.append(
                f"group {group_id!r} declares conflicting group_color values "
                f"({existing_color!r} and {group_color!r}); using {existing_color!r}."
            )

    for p, (part_id, id_source), (group_id, _group_name) in zip(
        raw_parts, part_id_sources, part_groups,
    ):
        entry: dict = {"id": part_id, "id_source": id_source}
        if p.get("name") is not None:
            entry["name"] = p["name"]
        group_color = groups_by_id.get(group_id, {}).get("color")
        if group_id is not None:
            entry["part_of"] = group_id
        else:
            entry["part_of"] = None
        if p.get("color") is not None:
            entry["color"] = p["color"]
        elif group_id is not None and group_color is not None:
            entry["color"] = group_color
        entry["metrics"] = compute_metrics(p["topo_shape"])
        parts_output.append(entry)
    _finish_phase("metrics", _t, "metrics_ms")
    groups_output = list(groups_by_id.values())

    glb_parts = [
        {**entry, "topo_shape": raw["topo_shape"]}
        for entry, raw in zip(parts_output, raw_parts)
    ]

    if metrics.get("warnings"):
        warnings.extend(metrics["warnings"])

    # Final geometry validity is part of core CAD success. Stop before STEP
    # export and before every visual/post-processing phase when it fails.
    from agentcad.core_build import invalid_geometry_payload

    invalid_response = invalid_geometry_payload("run", metrics)
    if invalid_response is not None:
        if dry_run:
            invalid_response.update({
                "runtime": runtime_name,
                "output_type": output_type,
            })
            if parts_output:
                invalid_response["parts"] = parts_output
            if groups_output:
                invalid_response["groups"] = groups_output
            if warnings:
                invalid_response["warnings"] = warnings
            _timings["total_ms"] = round(
                (time.perf_counter() - _t_total_start) * 1000
            )
            invalid_response["timings"] = _timings
            invalid_response["completed_phases"] = list(_phase_tracker.completed)
            invalid_response["phase_timings"] = dict(_timings)
            click.echo(json.dumps(invalid_response))
            sys.exit(1)
        _record_invalid_geometry(
            manifest,
            script_path,
            label,
            version_num,
            runtime_name,
            output_type,
            metrics,
            parts_output,
            groups_output,
            warnings,
            invalid_response,
        )

    # Dry-run: return metrics only, no version/disk artifacts
    if dry_run:
        output_json = {
            "command": "run",
            "status": "success",
            "runtime": runtime_name,
            "output_type": output_type,
            "metrics": metrics,
        }
        if parts_output:
            output_json["parts"] = parts_output
        if groups_output:
            output_json["groups"] = groups_output
        if warnings:
            output_json["warnings"] = warnings
        _timings["total_ms"] = round((time.perf_counter() - _t_total_start) * 1000)
        output_json["timings"] = _timings
        output_json["completed_phases"] = list(_phase_tracker.completed)
        output_json["phase_timings"] = dict(_timings)
        click.echo(json.dumps(output_json))
        return

    # Script succeeded — create version directory and write files
    dir_name = f"v{version_num}_{label}" if label != f"v{version_num}" else label
    version_dir = Path.cwd() / dir_name
    version_dir.mkdir(parents=True, exist_ok=True)

    # Copy script into version directory
    shutil.copy2(str(script_path), str(version_dir / "script.py"))

    # Export STEP file via the runner (each engine has its own writer)
    _heartbeat("exporting STEP…")
    _t = _start_phase("export_step")
    runner.export_step(shape, str(version_dir / "output.step"))
    _finish_phase("export_step", _t, "export_step_ms")

    # Core build boundary: execution, all metrics, validity, and STEP export
    # have succeeded. Everything below is post-processing or an explicitly
    # requested secondary export; Milestone 1.3 commits this core result here.

    # Export mesh formats if requested
    exports_meta = {}
    if export:
        _heartbeat("exporting requested mesh formats…")
        _t = _start_phase("export_mesh")
        formats = parse_export_formats(export)
        topo_shape = result.topo_shape
        for fmt in formats:
            if fmt == "stl":
                stl_path = version_dir / "output.stl"
                runner.export_stl(shape, str(stl_path))
                exports_meta["stl"] = f"{dir_name}/output.stl"
            elif fmt == "glb":
                from agentcad.export import export_glb

                glb_path = version_dir / "output.glb"
                export_glb(topo_shape, str(glb_path), parts=glb_parts)
                exports_meta["glb"] = f"{dir_name}/output.glb"
            elif fmt == "obj":
                from agentcad.export import export_obj

                obj_path = version_dir / "output.obj"
                export_obj(topo_shape, str(obj_path))
                exports_meta["obj"] = f"{dir_name}/output.obj"
        _finish_phase("export_mesh", _t, "export_mesh_ms")

    # Render views if requested
    renders_meta = {}
    if render:
        _heartbeat("rendering requested views…")
        _t = _start_phase("render_views")
        from agentcad.render import (
            parse_view_spec, render_shape as render_shape_view,
            render_shape_custom, render_views, ALL_VIEWS,
        )

        renders_dir = version_dir / "renders"
        renders_dir.mkdir(parents=True, exist_ok=True)
        topo_shape = result.topo_shape
        view_specs = parse_view_spec(render)
        for spec_type, spec_value in view_specs:
            if spec_type == "named":
                out_path = renders_dir / f"{spec_value}.png"
                render_shape_view(topo_shape, spec_value, out_path, parts=glb_parts)
                renders_meta[spec_value] = f"{dir_name}/renders/{spec_value}.png"
            else:
                az, el = spec_value
                name = f"{int(az)}_{int(el)}"
                out_path = renders_dir / f"{name}.png"
                render_shape_custom(topo_shape, az, el, out_path, parts=glb_parts)
                renders_meta[name] = f"{dir_name}/renders/{name}.png"
        _finish_phase("render_views", _t, "render_views_ms")

    # Visual feedback pipeline. Three tiers, decoupled by cost:
    #   1. Always (cheap, agent depends on these for the viewer to work):
    #      - GLB export
    #      - diff PNGs against the most recent successful prior version
    #      - unified viewer.html
    #   2. --preview (default on, ~2-4s, agent reads these as image data):
    #      - 4-view composite PNG
    #      - per-part preview PNGs
    #   3. On-demand only:
    #      - turntable GIF — captured client-side via the viewer's Export
    #        GIF button (three.js canvas + gif.js). Used for social posts,
    #        not for agent iteration.
    preview_meta = None
    diff_meta = None
    viewer_meta = None

    # Tier 2: 4-view composite + per-part previews (gated by --preview)
    if preview:
        from agentcad.render import render_composite_4view

        _heartbeat("rendering preview (4-view composite)…")
        _t = _start_phase("preview")
        preview_path = version_dir / "preview.png"
        render_composite_4view(
            topo_shape_for_metrics,
            preview_path,
            per_view_size=512,
            parts=glb_parts,
        )
        preview_meta = f"{dir_name}/preview.png"
        _finish_phase("preview", _t, "preview_ms")

        # Per-part previews use the resolved part id, which is unique and
        # path-safe even when display names are duplicated.
        if parts_output:
            from agentcad.render import render_shape as _render_part_iso

            parts_dir = version_dir / "parts"
            parts_dir.mkdir(exist_ok=True)
            _n_parts = len(parts_output)
            _heartbeat(
                f"rendering per-part previews ({_n_parts} part"
                f"{'s' if _n_parts != 1 else ''})…"
            )
            _t = _start_phase("parts_preview")
            for entry, raw in zip(parts_output, raw_parts):
                fname = f"{entry['id']}.png"
                _render_part_iso(
                    raw["topo_shape"],
                    "iso",
                    parts_dir / fname,
                    parts=[{**entry, "topo_shape": raw["topo_shape"]}],
                )
                entry["preview"] = f"{dir_name}/parts/{fname}"
            _finish_phase("parts_preview", _t, "parts_preview_ms")

    # Tier 1: GLB + diff + viewer.html — always run, regardless of --preview.
    # These are what make the viewer experience work; they're cheap enough
    # that there's no agent-side reason to opt out.
    from agentcad.export import export_glb
    from agentcad.commands.view import _render_unified

    viewer_glb_path = version_dir / "output.glb"
    if not viewer_glb_path.exists():
        _heartbeat("exporting viewer GLB…")
        _t = _start_phase("export_viewer_glb")
        export_glb(topo_shape_for_metrics, str(viewer_glb_path), parts=glb_parts)
        _finish_phase("export_viewer_glb", _t, "export_viewer_glb_ms")

    prev = _find_prev_success(versions)
    previous_parts = []
    part_changes = None
    if prev is not None:
        previous_meta_path = Path.cwd() / prev["path"] / "meta.json"
        try:
            previous_parts = json.loads(previous_meta_path.read_text()).get("parts", [])
        except (OSError, json.JSONDecodeError):
            previous_parts = []
        part_changes = _part_change_summary(previous_parts, parts_output, prev["label"])
    if prev is not None:
        from agentcad.render import (
            render_diff_side_by_side,
            render_diff_overlay,
        )
        prev_step_path = Path.cwd() / prev["path"] / "output.step"
        if prev_step_path.exists():
            _heartbeat("rendering diff against previous version…")
            _t = _start_phase("diff")
            try:
                from agentcad.step_io import load_cad_shape
                prev_shape = load_cad_shape(prev_step_path)

                side_path = version_dir / "diff_side.png"
                overlay_path = version_dir / "diff_overlay.png"
                render_diff_side_by_side(
                    prev_shape, topo_shape_for_metrics,
                    prev["label"], label, side_path,
                    width=512, height=512,
                    parts_b=glb_parts,
                )
                comparison = render_diff_overlay(
                    prev_shape, topo_shape_for_metrics,
                    prev["label"], label, overlay_path,
                    width=1024, height=1024,
                    parts_b=glb_parts,
                )
                from agentcad.solid_compare import (
                    compare_solid_volumes,
                    write_solid_comparison_artifacts,
                )

                solid_comparison = compare_solid_volumes(
                    prev_shape,
                    topo_shape_for_metrics,
                )
                diff_meta = {
                    "against": prev["label"],
                    "side_by_side": f"{dir_name}/diff_side.png",
                    "overlay": f"{dir_name}/diff_overlay.png",
                    "projection_comparison": comparison,
                    "comparison_3d": solid_comparison.data,
                }
                try:
                    volume_glb_path = version_dir / "diff_volume.glb"
                    volume_png_path = version_dir / "diff_volume.png"
                    if write_solid_comparison_artifacts(
                        solid_comparison,
                        volume_glb_path,
                        volume_png_path,
                    ):
                        diff_meta["volume_glb"] = (
                            f"{dir_name}/diff_volume.glb"
                        )
                        diff_meta["volume_png"] = (
                            f"{dir_name}/diff_volume.png"
                        )
                except Exception as exc:
                    warnings.append(
                        "Could not write 3D volume comparison artifacts: "
                        f"{type(exc).__name__}: {exc}"
                    )
            except Exception as e:
                warnings.append(
                    f"Could not render diff against v{prev['version']}_{prev['label']}: {type(e).__name__}: {e}"
                )
            finally:
                _finish_phase("diff", _t, "diff_ms")

    # Resolve the prior version's GLB for the viewer's side-by-side / overlay
    # modes. Decoupled from diff_meta: even if the diff PNG render failed,
    # we still want the 3D comparison to work in the viewer.
    prev_glb_path = None
    if prev is not None:
        candidate = Path.cwd() / prev["path"] / "output.glb"
        if candidate.exists():
            prev_glb_path = candidate
        else:
            prev_step_path = Path.cwd() / prev["path"] / "output.step"
            if prev_step_path.exists():
                try:
                    from agentcad.step_io import load_cad_shape as _load
                    prev_shape = _load(prev_step_path)
                    export_glb(prev_shape, str(candidate))
                    prev_glb_path = candidate
                except Exception:
                    prev_glb_path = None

    _heartbeat("writing viewer.html…")
    _t = _start_phase("viewer")
    viewer_path = version_dir / "viewer.html"
    _render_unified(
        viewer_path,
        glb_a=prev_glb_path or viewer_glb_path,
        glb_b=viewer_glb_path if prev_glb_path else None,
        label_a=prev["label"] if prev_glb_path else label,
        label_b=label if prev_glb_path else "",
        default_mode="side-by-side" if prev_glb_path else "single-a",
        preview_png=version_dir / "preview.png" if preview_meta else None,
        diff_side_png=version_dir / "diff_side.png" if diff_meta else None,
        diff_overlay_png=version_dir / "diff_overlay.png" if diff_meta else None,
        diff_volume_png=(
            version_dir / "diff_volume.png"
            if diff_meta and diff_meta.get("volume_png")
            else None
        ),
        parts=parts_output,
        parts_model="b" if prev_glb_path else "a",
        part_changes=part_changes,
        groups=groups_output,
    )
    viewer_meta = f"{dir_name}/viewer.html"
    viewer_glb_meta = f"{dir_name}/output.glb"
    _finish_phase("viewer", _t, "viewer_ms")

    viewer_opened = False
    if open_view:
        try:
            from agentcad.commands.view import _open_browser

            viewer_opened = _open_browser(viewer_path.resolve().as_uri()) is not False
        except Exception as exc:
            warnings.append(f"Could not open the review viewer: {type(exc).__name__}: {exc}")

    # Write meta.json
    created = datetime.now(timezone.utc).isoformat()
    meta = {
        "version": version_num,
        "label": label,
        "status": "success",
        "runtime": runtime_name,
        "output_type": output_type,
        "created": created,
        "script": f"{dir_name}/script.py",
        "outputs": {
            "step": f"{dir_name}/output.step",
            **exports_meta,
        },
    }
    meta["metrics"] = metrics
    if parts_output:
        meta["parts"] = parts_output
    if groups_output:
        meta["groups"] = groups_output
    if parsed_params:
        meta["params"] = parsed_params
    if warnings:
        meta["warnings"] = warnings
    if preview_meta:
        meta["preview"] = preview_meta
    if diff_meta:
        meta["diff"] = diff_meta
    if viewer_meta:
        meta["viewer"] = viewer_meta
        meta["viewer_glb"] = viewer_glb_meta
    if renders_meta:
        meta["renders"] = renders_meta
    meta["completed_phases"] = list(_phase_tracker.completed)
    meta["phase_timings"] = dict(_timings)
    meta_path = version_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    # Update manifest
    versions.append({
        "version": version_num,
        "label": label,
        "status": "success",
        "path": f"{dir_name}/",
    })
    manifest["versions"] = versions
    manifest["current"] = label
    save_manifest(manifest)

    hint = None
    if viewer_meta:
        if prev_glb_path:
            hint = (
                f"Review {viewer_meta} in the browser — A is the previous run, "
                f"B is this run, with side-by-side and overlay ready."
            )
        else:
            hint = f"Review {viewer_meta} in the browser."

    # Output success JSON
    output_json = {
        "command": "run",
        "status": "success",
        "runtime": runtime_name,
        "output_type": output_type,
        "version": version_num,
        "label": label,
        "outputs": {
            "step": f"{dir_name}/output.step",
            "script": f"{dir_name}/script.py",
            **exports_meta,
        },
    }
    output_json["metrics"] = metrics
    if parts_output:
        output_json["parts"] = parts_output
    if groups_output:
        output_json["groups"] = groups_output
    if parsed_params:
        output_json["params"] = parsed_params
    if warnings:
        output_json["warnings"] = warnings
    if preview_meta:
        output_json["preview"] = preview_meta
    if diff_meta:
        output_json["diff"] = diff_meta
    if viewer_meta:
        output_json["viewer"] = viewer_meta
        output_json["viewer_glb"] = viewer_glb_meta
        output_json["viewer_opened"] = viewer_opened
    if renders_meta:
        output_json["renders"] = renders_meta
    if hint:
        output_json["hint"] = hint
    _timings["total_ms"] = round((time.perf_counter() - _t_total_start) * 1000)
    output_json["timings"] = _timings
    output_json["completed_phases"] = list(_phase_tracker.completed)
    output_json["phase_timings"] = dict(_timings)
    click.echo(json.dumps(output_json))
