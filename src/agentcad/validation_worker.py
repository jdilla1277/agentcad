"""Private subprocess entry point for bounded whole-file validation.

Writes one JSON line per finished layer to the result path, then a final
``{"report": ...}`` line. The parent keeps the finished layers if it has to
kill this process when the budget expires.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 4:
        return 2
    source, result_path, profile, evidence_limit = args
    out = Path(result_path)
    try:
        from agentcad.native_io import suppress_native_output
        from agentcad.step_io import load_cad_shape
        from agentcad.validation import _LOADER_LAYERS, load_failure_report, validate_shape
        import time

        with out.open("a") as handle:
            def emit(name, entry):
                handle.write(json.dumps({"layer": name, "entry": entry}) + "\n")
                handle.flush()

            started = time.perf_counter()
            try:
                with suppress_native_output():
                    shape = load_cad_shape(Path(source))
            except Exception as exc:
                report = load_failure_report(
                    "file_parse", f"{type(exc).__name__}: {exc}", profile=profile
                )
                report["timings"] = {"reload_ms": int(round((time.perf_counter() - started) * 1000))}
                handle.write(json.dumps({"report": report}) + "\n")
                return 0
            reload_ms = int(round((time.perf_counter() - started) * 1000))
            for name in _LOADER_LAYERS:
                emit(name, {"status": "pass", "duration_ms": 0})
            started = time.perf_counter()
            # The whole report is under the parent's budget, so the mesh
            # layer runs here instead of spawning a nested worker.
            report = validate_shape(
                shape, profile=profile, in_process=True,
                evidence_limit=int(evidence_limit), on_layer=emit,
            )
            report["timings"] = {
                "reload_ms": reload_ms,
                "delivered_validation_ms": int(round((time.perf_counter() - started) * 1000)),
            }
            handle.write(json.dumps({"report": report}) + "\n")
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
