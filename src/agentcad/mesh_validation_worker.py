"""Private subprocess entry point for the bounded mesh-manifold layer."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 4:
        return 2
    brep_path, result_path, deflection, evidence_limit = args
    try:
        from agentcad.step_io import load_cad_shape
        from agentcad.validation import mesh_manifold_report

        shape = load_cad_shape(Path(brep_path))
        entry = mesh_manifold_report(
            shape, float(deflection), evidence_limit=int(evidence_limit)
        )
        Path(result_path).write_text(json.dumps(entry))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
