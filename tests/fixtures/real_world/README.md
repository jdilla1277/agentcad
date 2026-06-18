# Real-world CAD fixtures

STEP / BREP files exported from actual CAD tools (Fusion, Onshape,
SolidWorks). They're meaningfully different from cadquery-generated
STEPs — AP214 schema variation, named features, multi-component
assemblies, import-orientation skew — and these differences catch
bugs synthetic fixtures cannot.

## Why fixtures-in-git, not cached download

Real CAD files are rare and slow to come by. Caching from a remote
URL works but adds a network dependency to the test run, and
"unreachable URL" failures pollute friction logs. For files <1MB,
committing them is the cheaper failure mode. If we accumulate many
or larger files, switch to git-lfs.

## Files

### `pump_manifold.step` (504KB, AP214)

Source: Fusion 360 export of a Pump Manifold v3 design (own work,
no IP encumbrance). 118 faces, 334 edges, single closed solid.

Used by:
- The M60 Phase 4 e2e friction test (PR #127 retro), which surfaced
  the `face_count` misleading-on-real-parts finding (#130), the
  `inspect --ids` JSON-size finding (#131), and the
  `face_orientations` alarm finding (fixed in PR #127's follow-up).
- `tests/test_subprocess_contract.py` — the load-bearing input
  for verifying the JSON-on-stdout contract holds against real
  AP214 input, not just synthetic cadquery exports.

### `cadgenbench_101_agentcad.step` (556KB, STEP)

Source: CADGenBench task 101 output from internal AgentCAD run
`20260615_091000_openai_gpt-5.5_agentcad`. 150 faces, 427 edges,
single valid solid with measured dimensions 220 x 120 x 45 mm.

Used by:
- `tests/test_measure.py` — the M65 Slice 1 real-output acceptance
  check that `agentcad measure` reports the known Ø12, Ø36, and Ø50
  cylindrical feature buckets from CADGenBench 101.

## Adding new fixtures

Keep them small (<1MB ideal, <5MB hard cap). Add an entry above
naming the source, the format/schema, basic topology counts, and
which tests use it. License must be unambiguous — own work or
permissively-licensed sources only.
