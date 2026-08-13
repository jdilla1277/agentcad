"""Private subprocess entry point for bounded exact 3D comparison."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _write_shape(shape, path):
    from OCP.BRepTools import BRepTools

    if not BRepTools.Write_s(shape, str(path)):
        raise RuntimeError(f"Could not write worker result {path.name}")


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 4:
        return 2
    reference_path = Path(args[0])
    candidate_path = Path(args[1])
    result_dir = Path(args[2])
    tolerance_mm = float(args[3])

    try:
        from agentcad.solid_compare import compare_solid_volumes
        from agentcad.step_io import load_cad_shape

        comparison = compare_solid_volumes(
            load_cad_shape(reference_path),
            load_cad_shape(candidate_path),
            tolerance_mm=tolerance_mm,
        )
        (result_dir / "result.json").write_text(json.dumps(comparison.data))
        if comparison.available:
            volumes = comparison.data["volumes"]
            for name, shape in (
                ("shared", comparison.shared_shape),
                ("reference_only", comparison.reference_only_shape),
                ("candidate_only", comparison.candidate_only_shape),
            ):
                if shape is not None and volumes[name] > 0:
                    _write_shape(shape, result_dir / f"{name}.brep")
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
