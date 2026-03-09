# cadtool v0 — Implementation Plan

Milestones are ordered by dependency. Each milestone is a shippable increment. Red/green TDD throughout — every feature starts with a failing test.

---

## Milestone Summary

| Milestone | Description | Status |
|-----------|-------------|--------|
| M1 | Project scaffolding & `cadtool init` | Done |
| M2 | 2D geometry primitives (`add-rect`, `add-circle`, `list`) | Done |
| M3 | Geometry enrichment (`--label`, `count`, `delete`, `get`) | Done |
| M4 | `cadtool run` — CadQuery script execution & STEP export | Done |
| M5 | Error handling & failed versions | Done |
| M6 | PNG rendering | Done |
| M7 | GLB & STL export | Done |
| M8 | `cadtool render` (custom views, `--zoom`) | Done |
| ~~M9~~ | ~~2D primitive cleanup~~ | Done (absorbed into M4) |
| M9b | `--focus` for `cadtool render` | Done |
| M10 | `cadtool context`, `cadtool docs` & `cadtool diff` | Done |
| M11 | Geometry helpers (`loft_sections`, `tapered_sweep`, `naca_wire`, `mirror_fuse`) | Done |
| M12 | `cadtool export` command | Done |
| M13 | OBJ export & end-to-end verification | Done |
| M14 | Geometric metrics in build output (fast-loop epic) | Done |
| M15 | Script preamble — implicit runtime context (fast-loop epic) | Done |
| M16 | Pre-execution validation (fast-loop epic) | Done |
| M17 | Friction fixes — auto-compound, docs improvements | Done |

---

## Milestone 1: Project Scaffolding & `cadtool init` ✓

**Goal:** A pip-installable CLI that runs `cadtool init` and produces correct project structure.

See [m1_plan.md](m1_plan.md) for full plan.

**Delivered:** 12 tests, `cadtool init` command with JSON output, Click CLI, pytest suite.

---

## Milestone 2: 2D Geometry Primitives ✓

**Goal:** Let agents create and inspect geometry via `add-rect`, `add-circle`, and `list`.

See [m2_plan.md](m2_plan.md) for full plan.

**Delivered:** 38 tests, 4 commands (`init`, `add-rect`, `add-circle`, `list`), shared manifest module, auto-incrementing IDs.

---

## Milestone 3: Geometry Enrichment ✓

**Goal:** Labels, count, delete, and get — close the CRUD gaps before moving to 3D.

See [m3_plan.md](m3_plan.md) for full plan.

**Delivered:** 58 tests, 6 commands (`init`, `add-rect`, `add-circle`, `list`, `delete`, `get`), optional `--label` on shapes, `count` in list response.

---

## Milestone 4: `cadtool run` — Script Execution & STEP Export ✓

**Goal:** Execute a CadQuery script and produce a versioned STEP file.

See [m4_plan.md](m4_plan.md) for full plan.

**Delivered:** 25 tests, 2 commands (`init`, `run`). CadQuery 2.7.0 via CQGI, STEP export, versioned directories, meta.json, manifest tracking. 2D primitives removed (M9 absorbed). Failed scripts produce no disk artifacts. Python 3.12 venv (OCP requirement).

---

## Milestone 5: Error Handling & Failed Versions ✓

**Goal:** Failed runs are preserved with `_failed` suffix, error details in JSON.

See [m5_plan.md](m5_plan.md) for full plan.

**Delivered:** 30 tests, 2 commands (`init`, `run`). Script failures (syntax errors, runtime errors, missing `show_object()`) create `_failed` directories with `script.py` + `meta.json`, tracked in manifest with `status: "failed"`. Failed runs consume version numbers; `current` does not advance. CLI errors unchanged.

---

## Milestone 6: PNG Rendering ✓

**Goal:** Produce PNG renders from CadQuery results for agent visual inspection.

See [m6_plan.md](m6_plan.md) for full plan.

**Delivered:** 43 tests, 2 commands (`init`, `run`). OCP offscreen rendering via `V3d_View.ToPixMap` with software GL, shaded mode. `--render` option on `cadtool run` accepts comma-separated views (`iso`, `front,top`, `all`). Renders saved to `vN_label/renders/`, paths recorded in meta.json and CLI JSON output. 7 views available (front, back, left, right, top, bottom, iso); `all` expands to the standard 4 (front, right, top, iso). No renders on failed runs.

---

## Milestone 7: GLB & STL Export ✓

**Goal:** Mesh exports for web viewers, 3D printing, and agent interop.

See [m7_plan.md](m7_plan.md) for full plan.

**Delivered:** 53 tests, 2 commands (`init`, `run`). `--export stl,glb` option on `cadtool run`. STL via CadQuery `exporters.export()`. GLB via OCP `RWGltf_CafWriter` (tessellate → XCAF doc → binary glTF). STEP always produced; `--export` adds mesh formats alongside. Export paths in `outputs` in meta.json and CLI JSON. OBJ deferred to M12.

---

## Milestone 8: `cadtool render` ✓

**Goal:** Render additional views of an existing STEP file without creating a new version.

See [m8_plan.md](m8_plan.md) for full plan.

**Delivered:** 74 tests, 3 commands (`init`, `run`, `render`). `cadtool render <step_path> --view <spec>` renders PNGs from existing STEP files. Named views (`iso`, `front,top`, `all`), custom angles (`--view 45,30` as azimuth,elevation), `--zoom` factor, `--name` for custom filenames. Version directory detection (regex `v\d+_\w+` + meta.json) saves to `renders/` and updates meta.json; standalone STEP files get PNGs alongside. `render.py` refactored with `_setup_render()` helper, `parse_view_spec()`, and `render_shape_custom()`. `--focus` deferred to M9.

---

## ~~Milestone 9: 2D Primitive Cleanup~~ ✓ (absorbed into M4)

Completed as part of M4. Removed `add-rect`, `add-circle`, `list`, `delete`, `get` commands, `objects`/`next_id` from manifest, and all associated tests.

---

## Milestone 9b: `--focus` for `cadtool render` ✓

**Goal:** Make `cadtool render` zoom usable for detail inspection.

### Context

M8 friction testing (`feedback/2026-03-08/friction-log-cadtool-render.md`) revealed that `--zoom` on thin orthographic views (e.g. front view of a flat part) produces unusable close-ups because zoom magnifies from the frame center after `FitAll`. Without a way to shift the camera target, agents can't reliably zoom into specific features. `--focus` solves this.

The friction log also noted that `--view all` overwrites same-named renders — this is correct behavior. Standard view names are "latest"; agents use `--name` to protect important renders.

**Delivered:** 84 tests (74 → 84), 3 commands. `--focus x,y,z` sets camera target via `view.SetAt()`. `--no-fit` skips `FitAll()` for exact framing with `--focus` + `--zoom`. `_apply_camera()` helper extracted in render.py to share focus/fit/zoom logic across `render_shape()` and `render_shape_custom()`.

---

## Milestone 10: `cadtool context`, `cadtool docs` & `cadtool diff` ✓

**Goal:** Agent discoverability and version comparison commands.

**Delivered:** 111 tests (84 → 111), 6 commands (`init`, `run`, `render`, `context`, `docs`, `diff`). `cadtool context` returns project state (name, version count, current label, tool version). `cadtool docs [section]` returns hardcoded documentation across 6 sections (commands, render, export, schema, helpers, workflow). `cadtool diff <ref1> <ref2>` compares two versions by number or label, showing scalar diffs and set diffs for outputs/renders.

---

## Milestone 11: Geometry Helpers ✓

**Goal:** Reusable organic geometry primitives for CadQuery scripts.

### Context

The original M11 plan was "Assembly & Multi-Part Rendering" but real-world friction testing showed that agents needed organic shape primitives more urgently than assembly commands. The scope pivoted to a helpers module.

**Delivered:** 141 tests (111 → 133 before M12), `cadtool.helpers` module with 4 functions:
- `loft_sections(sections, smooth=True)` — loft through TopoDS_Wire sections to produce a solid
- `tapered_sweep(spine, radii)` — loft circular sections along a spine with varying radii
- `naca_wire(y, le_x, te_x, thickness, profile='0012')` — NACA 4-digit closed-TE airfoil wire
- `mirror_fuse(shape, plane='XZ')` — mirror and fuse about a coordinate plane

Docs section added to `cadtool docs helpers`.

---

## Milestone 12: `cadtool export` Command ✓

**Goal:** Post-hoc mesh export from existing STEP files without re-running scripts.

### Context

Friction log from the Empire State Building exercise revealed a workflow gap: after `cadtool run` produces a STEP file, there was no CLI path to export it to mesh formats after the fact. Agents had to re-run scripts or drop to raw Python (which caused venv traps).

**Delivered:** 141 tests (133 → 141), 7 commands. `cadtool export <step_file> --format stl,glb` imports a STEP file and exports to requested mesh formats. Output files go next to the STEP file. Version directory detection updates meta.json outputs. Invalid formats return JSON error.

---

## Milestone 13: OBJ Export & End-to-End Verification ✓

**Goal:** OBJ export format and end-to-end workflow test.

**Delivered:** 149 tests (141 → 149), 7 commands. `export_obj()` in `export.py` using manual triangle extraction (`BRepMesh_IncrementalMesh` → `TopExp_Explorer` with `TopoDS.Face_s()` downcast → `Poly_Triangulation` → `v`/`vn`/`f` lines with per-face normals). OBJ wired into both `cadtool export --format obj` and `cadtool run --export obj`. End-to-end workflow test covers init → run → render → export → context → diff. Distribution works via `pip install git+...` (pyproject.toml entry point + deps already configured).

---

## Dependency Graph

```
M1 (init) → M4 (run/STEP) → M5 (errors) → M6 (PNG) → M7 (GLB/STL) → M8 (render)
                                                                           ↓
M1 ──────────────────────────────────────────────── M10 (context/docs/diff)
                                                                           ↓
                                                    M11 (helpers) + M12 (export cmd)
                                                                           ↓
                                                         M13 (distribution + OBJ)
```

M1 is the foundation. M4 delivered the core 3D pipeline (and absorbed M9 cleanup). M5-M8 build out the rendering and export pipeline. M10 adds agent discoverability. M11 pivoted from assemblies to geometry helpers based on friction testing. M12 added standalone `cadtool export` for post-hoc mesh conversion. M13 is the remaining work: distribution verification and OBJ export. M2-M3 (2D primitives) were scaffolding milestones — code has been removed.

M14-M16 are Phase 1 of the [fast-loop epic](fast-loop/overview.md) — cheap wins to reduce wasted iterations.

---

## Milestone 14: Geometric Metrics in Build Output

**Epic:** [Fast Loop](fast-loop/overview.md) | **Plan:** [m14_metrics.md](fast-loop/m14_metrics.md)

**Goal:** Every successful `cadtool run` returns geometric metrics (bbox, volume, area, face/edge counts, validity) so agents can verify shape correctness without rendering.

---

## Milestone 15: Script Preamble

**Epic:** [Fast Loop](fast-loop/overview.md) | **Plan:** [m15_preamble.md](fast-loop/m15_preamble.md)

**Goal:** Scripts run with `cq`, `show_object`, and all helpers pre-injected. Agent writes pure design intent, zero import boilerplate.

---

## Milestone 16: Pre-Execution Validation

**Epic:** [Fast Loop](fast-loop/overview.md) | **Plan:** [m16_validation.md](fast-loop/m16_validation.md)

**Goal:** Catch preventable mistakes (syntax errors, missing `show_object()`, bad imports) in <100ms before the expensive build, without consuming a version number.

---

## Milestone 17: Friction Fixes — Auto-Compound & Docs

**Goal:** Fix silent data loss from multiple `show_object()` calls and fill documentation gaps exposed by the Golden Gate Bridge friction log.

**Delivered:** 202 tests (190 → 202), 7 commands. Five fixes:
1. **Auto-compound** — `cadtool run` now combines multiple `show_object()` results into a single `cq.Compound.makeCompound()`. Warning added to JSON output and meta.json.
2. **Quickstart docs** — New `cadtool docs quickstart` section with minimal example and multi-show_object pattern.
3. **Units note** — `cadtool docs metrics` documents that cadtool is unit-agnostic.
4. **tapered_sweep limitation** — `cadtool docs helpers` documents that tapered_sweep works best with smooth spines.
5. **Type conversion patterns** — `cadtool docs helpers` documents `cq.Shape.cast()` and `cq.Compound.makeCompound()` patterns.
