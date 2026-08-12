"""Observable phase contract shared by CAD comparison workflows."""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator


COMPARISON_PHASES = (
    "source_loading",
    "comparison_rendering",
    "projection_comparison",
    "exact_3d_comparison",
    "difference_artifact_export",
    "viewer_generation",
)

_VALID_STATUSES = {
    "pending",
    "success",
    "unavailable",
    "timeout",
    "failed",
    "skipped",
}


@dataclass
class PhaseObservation:
    """Mutable outcome selected by code running inside an observed phase."""

    status: str = "success"
    message: str | None = None


class ComparisonPhaseRecorder:
    """Record stable per-stage status and elapsed time for a comparison.

    ``entries`` may be attached directly to a command response or committed
    ``meta.json``. Optional callbacks let ``agentcad run`` mirror these stages
    into its existing watchdog timing contract without coupling other commands
    to the run implementation.
    """

    def __init__(
        self,
        entries: dict | None = None,
        *,
        on_start: Callable[[str], None] | None = None,
        on_finish: Callable[[str, dict], None] | None = None,
        persist: Callable[[], None] | None = None,
    ):
        self.entries = entries if entries is not None else {
            name: {"status": "pending"} for name in COMPARISON_PHASES
        }
        for name in COMPARISON_PHASES:
            self.entries.setdefault(name, {"status": "pending"})
        self._on_start = on_start
        self._on_finish = on_finish
        self._persist = persist

    @contextmanager
    def observe(self, name: str) -> Iterator[PhaseObservation]:
        """Time one attempted phase and attribute ordinary exceptions to it."""
        self._require_phase(name)
        previous = self.entries[name]
        self.entries[name] = {"status": "pending"}
        if self._on_start is not None:
            self._on_start(name)
        started = time.perf_counter()
        observation = PhaseObservation()
        try:
            yield observation
        except Exception as exc:
            self._finish(
                name,
                started,
                "failed",
                f"{type(exc).__name__}: {exc}",
                previous,
            )
            raise
        else:
            self._finish(
                name,
                started,
                observation.status,
                observation.message,
                previous,
            )

    def skip(self, name: str, message: str) -> None:
        self._require_phase(name)
        self.entries[name] = {"status": "skipped", "message": message}
        self._notify_finish(name)

    def finalize_pending(self, message: str) -> None:
        for name in COMPARISON_PHASES:
            if self.entries[name].get("status") == "pending":
                self.entries[name] = {
                    "status": "skipped",
                    "message": message,
                }
                self._notify_finish(name)

    def _finish(
        self,
        name: str,
        started: float,
        status: str,
        message: str | None,
        previous: dict,
    ) -> None:
        if status not in _VALID_STATUSES - {"pending"}:
            raise ValueError(f"Unsupported comparison phase status: {status}")
        duration_ms = round((time.perf_counter() - started) * 1000)
        if previous.get("status") == "success":
            duration_ms += previous.get("duration_ms", 0)
        entry = {
            "status": status,
            "duration_ms": duration_ms,
        }
        if message:
            entry["message"] = message
        self.entries[name] = entry
        self._notify_finish(name)

    def _notify_finish(self, name: str) -> None:
        if self._on_finish is not None:
            self._on_finish(name, self.entries[name])
        if self._persist is not None:
            self._persist()

    @staticmethod
    def _require_phase(name: str) -> None:
        if name not in COMPARISON_PHASES:
            raise KeyError(f"Unknown comparison phase: {name}")
