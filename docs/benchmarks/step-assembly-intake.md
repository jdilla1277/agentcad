# STEP assembly intake

`agentcad.step_assembly.read_step_assembly` reads STEP geometry and its document
structure in one OCCT transfer. It preserves named definitions separately from
their placed occurrences, including nested paths, composed placements, source
length-unit names, and Unicode labels. The implementation uses the default
OCP stack and adds no dependency.

This is an internal intake primitive. Existing import/inspect commands still
use their existing paths; the returned records are not a versioned inspection
manifest or a default agent report.

## Geometry and identity

- A definition retains its geometry in its local coordinate frame. Each
  occurrence has a distinct source path and a row-major 3 × 4 placement into
  assembly coordinates, normalized to millimetres.
- Source label entries and paths are scoped to the exact input document.
  Numeric solid/face/edge members address the returned `shape` using the same
  indexed maps as `agentcad.topo_ids`.
- `source_exact` certifies membership in that source shape. Re-exporting STEP
  or editing geometry can change traversal IDs. Verify correspondence against
  the normalized baseline before publishing version-local IDs or selectors.
- Repeated definitions are retained once; their occurrences remain distinct.
  Shared topology is marked `shared_topology`, so its raw numeric membership
  cannot be treated as an exclusive instance selection.
- `hierarchy_status="flat_roots"` indicates that the document has no assembly
  structure. Root geometry alone does not recover supplier component intent.
- Names remain unmodified. `name_status` distinguishes missing text, known
  generated patterns, and other document labels. A document label does not
  prove a human supplier authored the name. Resolve an automatically named
  reference through its definition when choosing a display name.
- The frozen records borrow mutable kernel geometry. Do not mutate that
  geometry while relying on its recorded memberships. This reader parses
  geometry; kernel/deliverable validation remains a separate operation.

## Reproduce

From an environment with the repository's default CAD dependencies:

```sh
PYTHONPATH=src python scripts/benchmark_step_assembly.py
PYTHONPATH=src python -m pytest tests_b3d/test_step_assembly.py
```

The benchmark defaults to two real workflow fixtures and two generated inputs:
100 references to one box definition, and one source part containing 150
disjoint boxes (900 faces). The latter stresses indexing and membership; it
does not represent a complex 900-face machined part. Pass paths explicitly to
measure other STEP files. Native writer diagnostics go to stderr; stdout is
JSON.

## Measurements

Measured on 2026-10-23, Apple M4, macOS 15.6, Python 3.12.12, build123d 0.10.0,
cadquery-ocp 7.8.1.1.post1. Each median uses three same-process passes after an
initial read. These are warm process-local measurements, not cold filesystem
or complete import/report timings.

| Input | Faces | Part definitions / leaf occurrences | Read + membership | Face/edge measurements |
| --- | ---: | ---: | ---: | ---: |
| CADGenBench 101 | 150 | 1 / 1 | 0.054 s | 0.015 s |
| Pump manifold | 118 | 1 / 1 | 0.100 s | 0.053 s |
| Generated repeated assembly | 600 | 1 / 100 | 0.010 s | 0.032 s |
| Generated multi-solid part | 900 | 1 / 1 | 0.248 s | 0.055 s |

Peak RSS for the entire benchmark process was approximately 348 MiB. Median
numeric face lookup remained below 0.07 ms on these fixtures. Validation,
recognition, rendering, persistence, remote transfer, and Python startup are
excluded. These measurements establish no full-report latency guarantee.

## Selection checks

Regression fixtures verify nested names and placements, noncommuting parent
and child rotations, distinct coincident source instances, multiple free
roots, malformed input, and metre-to-millimetre conversion. Both real fixtures
retain the same measured face/edge records through the existing shape loader
and the build123d editing loader.

For the nested assembly fixture, source records are checked against normalized
STEP after reload, then an edge ID is used to fillet one instance in build123d.
The other instance has zero occupied-space difference in both Boolean
directions. These checks demonstrate the tested correspondences; they do not
establish general ID continuity across normalization or edits.
