# M14 — Geometric Metrics

**Epic:** Fast Loop
**Status:** Planned
**Goal:** Every successful `cadtool run` returns geometric metrics so agents can verify shape correctness without rendering.

## New Files

- `app/src/cadtool/metrics.py` — `compute_metrics(topo_shape)` returning a dict
- `app/tests/test_metrics.py` — unit tests for each metric

## Modified Files

- `app/src/cadtool/commands/run.py` — call `compute_metrics()` after build, add to JSON + meta.json
- `app/src/cadtool/commands/diff.py` — compare metrics between versions
- `app/src/cadtool/commands/docs.py` — add "metrics" section

## Metrics

| Metric | OCP API | Return type |
|--------|---------|-------------|
| `bounding_box` | `Bnd_Box` + `BRepBndLib.Add_s()` | `{x: [min,max], y: [...], z: [...]}` |
| `dimensions` | derived from bbox | `{x: float, y: float, z: float}` |
| `volume` | `GProp_GProps` + `BRepGProp.VolumeProperties_s()` | `float` |
| `surface_area` | `GProp_GProps` + `BRepGProp.SurfaceProperties_s()` | `float` |
| `center_of_mass` | `GProp_GProps.CentreOfMass()` | `{x, y, z}` |
| `face_count` | `TopExp_Explorer(shape, TopAbs_FACE)` | `int` |
| `edge_count` | `TopExp_Explorer(shape, TopAbs_EDGE)` | `int` |
| `is_valid` | `BRepCheck_Analyzer(shape).IsValid()` | `bool` |

## Tests (~12)

- Unit: one test per metric on a known box shape
- Unit: metrics on a cylinder (different values than box)
- Integration: `cadtool run` output includes `metrics` key
- Integration: meta.json includes `metrics`
- Integration: `cadtool diff` shows metric changes between versions
- Integration: failed runs do NOT include metrics
