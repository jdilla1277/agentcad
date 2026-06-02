"""Script runners — one per backing CAD library.

Each runner exposes the same interface so the `run` command can route
without knowing which engine is in play:

    PREAMBLE: str            # implicit imports prepended to every script
    with_preamble(src) -> str
    validate(src) -> list    # pre-execution checks, returns error list
    execute(src, params) -> ExecutionResult

Adding a runner means adding a module here and wiring it into the
dispatcher (Phase 2). Today: `cadquery` is the only runner the CLI
actually calls; `build123d` lives alongside and is exercised by
`tests_b3d/` only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Status = Literal["success", "validation_error", "execution_error"]


@dataclass
class ExecutionResult:
    """Outcome of running a user script through a runner.

    ``status`` distinguishes the two failure modes `run.py` handles
    differently:

    * ``validation_error`` — pre-execution problem (bad param format,
      unknown param name). No version directory is created.
    * ``execution_error`` — the script raised or produced no results.
      ``run.py`` records a failed version directory.
    * ``success`` — ``native_shape`` and ``topo_shape`` are populated.
    """

    status: Status
    native_shape: Any = None
    topo_shape: Any = None
    parameters: dict[str, Any] = field(default_factory=dict)
    discovered_parameters: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    exception: str | None = None
    traceback: str | None = None
    # Per-part breakdown. One entry per show_object() call, declaration order.
    # Each dict has: id (int, sequential), name (str | None), color (str | None),
    # topo_shape (TopoDS_Shape). run.py enriches with metrics + preview path.
    parts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.status == "success"
