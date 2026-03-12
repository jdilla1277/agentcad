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
| M18 | Quick preview mode (fast-loop Phase 2) | Done |
| M19 | Multi-solid colored GLB export | Done |
| M20 | Patterns docs, positioning helpers & `cadtool view` | Done |
| M21 | Parametric scripts (fast-loop Phase 2) | Done |
| M22 | Friction fixes — Python version check, rotate docs, dry-run | Done |
| M23 | Persistent worker / daemon mode (fast-loop Phase 2) | Done |
| M24 | Friction fixes — geometry debugging, render quality, bug fixes | Done |

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

M14-M16 are Phase 1 of the [fast-loop epic](fast-loop/overview.md) — cheap wins to reduce wasted iterations. M17 addressed friction discovered during real model testing. M18 added quick preview for faster iteration. M19-M20 address output quality and agent footguns surfaced by friction logs. M21-M22 are iteration speed optimizations (parametric scripts, daemon).

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

---

## Milestone 18: Quick Preview Mode ✓

**Epic:** [Fast Loop](fast-loop/overview.md) | **Plan:** [m18_preview.md](fast-loop/m18_preview.md)

**Goal:** `--preview` flag on `cadtool run` produces a fast 256x256 iso PNG for shape verification during iteration. Ephemeral — not tracked in render history.

**Delivered:** 210 tests (202 → 210), 7 commands. `--preview` flag on `cadtool run` renders a 256x256 iso PNG to `vN_label/preview.png`. Preview path added to output JSON and meta.json as top-level `"preview"` key (not under `"renders"`). `--preview` and `--render` coexist independently. Docs updated with `--preview` in commands section.

---

## Milestone 19: Multi-Solid Colored GLB Export ✓

**Goal:** GLB exports preserve individual solids with per-part colors. Agents build multi-part assemblies; humans reviewing the output need to distinguish parts visually.

**Motivation:** Landing gear friction log — 7-part assembly exports as indistinguishable gray blob. STEP preserves separate solids but GLB flattens everything. The XCAF framework already in `export_glb()` natively supports multi-shape docs with `XCAFDoc_ColorTool`.

**Delivered:** 215 tests (210 → 215), 7 commands. `export_glb()` decomposes compounds into individual solids via `TopExp_Explorer(TopAbs_SOLID)`, adds each as a separate XCAF shape with a distinct color from a 10-color palette via `XCAFDoc_ColorTool.SetColor()`. Fallback: shapes with no solids (shell/face) are added as-is. Export docs updated to mention per-solid coloring.

---

## Milestone 20: Patterns Docs, Positioning Helpers & `cadtool view` ✓

**Goal:** Four friction fixes from the landing gear log — eliminate positioning footguns with docs and helpers, make GLB output viewable with zero friction, and allow mixed view specs.

**Motivation:** Landing gear friction log (F1, F2, F3, F6). Agents waste iterations on workplane/transform confusion. Users can't view GLB output on macOS without third-party tools.

**Delivered:** 234 tests (215 → 234), 8 commands. Four deliverables:
1. **`cadtool docs patterns`** — New docs section covering build-at-origin-then-position, revolve axis gotchas, compound vs union, and workplane positioning.
2. **`translate(shape, x, y, z)` + `rotate(shape, axis, angle_deg)` helpers** — Positioning primitives wrapping `gp_Trsf` + `BRepBuilderAPI_Transform`. Added to preamble, docs, and helpers section.
3. **`cadtool view <file>`** — Opens GLB/STEP in browser via self-contained HTML with three.js CDN. STEP auto-exports to GLB. Returns JSON with file URL.
4. **Mixed view specs** — `--view front,right,45:15` with colon syntax for angles. Backward-compatible with legacy `45,30` format.

---

## Milestone 21: Parametric Scripts ✓

**Epic:** [Fast Loop](fast-loop/overview.md) | **Plan:** [m21_plan.md](m21_plan.md)

**Goal:** `--params key=val,key=val` on `cadtool run` passes parameter overrides to CQGI. Agents iterate by changing numbers, not rewriting code.

**Delivered:** 247 tests (234 → 247), 8 commands. `--params length=60,width=20` on `cadtool run` overrides top-level script constants via CQGI's AST-level parameter injection. Type coercion: bool → int → float → str. Unknown params and bad format return `status: "error"` before version allocation. Params recorded in output JSON and meta.json. `cadtool diff` shows param changes. New `cadtool docs parametric` section.

---

## Milestone 22: Friction Fixes — Desk Lamp Friction Log ✓

**Goal:** Fix the P0 and P1 issues surfaced by the desk lamp agent friction test.

**Source:** [Desk lamp friction log](/Users/jamesdillard/Documents/friction-log.md)

**Delivered:** 254 tests (247 → 254), 8 commands. Four deliverables:
1. **P0 — Python version check** — `cadtool run` checks `sys.version_info >= (3, 13)` after `load_manifest()`, before any CadQuery imports. Returns clear error with found version and required range. No version consumed.
2. **P1 — `rotate()` direction convention** — Docstring and `cadtool docs helpers` now document right-hand rule: positive angle = counterclockwise when looking from positive axis toward origin.
3. **P2 — `--dry-run` mode** — `cadtool run script.py --output label --dry-run` executes the script, computes metrics, and returns them without creating a version directory, consuming a version number, or writing any disk artifacts.
4. **P2 — Angled positioning example** — `cadtool docs patterns` includes a worked example showing build-at-origin, rotate, translate pattern for tilted parts.

---

## Milestone 23: Persistent Worker (Daemon Mode) ✓

**Epic:** [Fast Loop](fast-loop/overview.md)

**Goal:** Background process keeps CadQuery/OCP loaded in memory. `cadtool run` auto-routes to daemon if running, eliminating 3-5s cold start per invocation.

**Delivered:** 28 new tests (254 → ~282), 9 commands (added `daemon` group with `start`/`stop`/`status`). `daemon.py` module with Unix domain socket IPC (length-prefixed JSON protocol), `DaemonServer` class with `handle_request()` dispatch, eager module warm-up at startup. `cadtool run` auto-routes through daemon when available, falls back to direct execution when not. `CADTOOL_DAEMON` env var prevents recursive routing. `start_daemon()` launches subprocess via `python -m cadtool.daemon`, PID file for lifecycle tracking, stale socket cleanup. New `cadtool docs daemon` section.

---

## Milestone 24: Friction Fixes — Helical Gear Friction Log ✓

**Source:** [Helical gear friction log](../feedback/2026-03-10/friction-log-helical-gear.md)

**Goal:** Fix geometry debugging gaps and quality-of-life issues surfaced by the helical gear agent friction test. The agent spent ~15 of 20 minutes diagnosing a hollow gear with no debugging tools.

**Delivered:** ~20 new tests (284 → ~304), 10 commands (added `inspect`). Seven fixes:
1. **`is_valid` diagnostics** — When `is_valid` is false, `compute_metrics()` returns `validity_errors` list with `BRepCheck_Status` names. Iterates all sub-shapes via `TopExp_Explorer`.
2. **Negative volume warning** — `compute_metrics()` returns `warnings` list when `volume < 0`: "Negative volume detected — shape may have inverted normals."
3. **`cadtool inspect` command** — Reports solid/shell/face/edge counts, per-shell open/closed status, face orientations (forward/reversed), free edge count, and BRepCheck validity.
4. **Brighter renders** — Ambient light bumped from 0.3 to 0.5, added fill light from (-1, 1, 0.5) at 0.4 intensity. Surfaces clearly visible from all angles.
5. **`cadtool view` relative path** — `Path(file).resolve()` so relative paths work with file URIs.
6. **Version directory collision** — `exist_ok=True` on `mkdir()` in both success and failure paths.
7. **Custom angles in `--render` on `cadtool run`** — Uses `parse_view_spec()` to support `--render 45:30` and mixed specs like `--render front,45:30`. New `cadtool docs inspect` section.

---

## Milestone 25: `--help` Operational Briefing & README

**Source:** Agent friction testing — first contact experience.

**Goal:** Make `cadtool --help` a complete operational briefing so an AI agent can be productive on first contact without discovering `cadtool docs`. Rewrite README for human-first agent-delegated workflow.

**Status:** In Progress (PR #15)

---

# v0.2 Roadmap — Friction-Driven

Open friction points from all test sessions, organized as small shippable milestones.

**Sources:**
- [Helical gear friction log (2026-03-10)](../feedback/2026-03-10_15-56-10.md) — latest
- [Helical gear friction log (2026-03-10)](../feedback/2026-03-10/friction-log-helical-gear.md) — original
- [Landing gear friction log (2026-03-09)](../feedback/2026-03-09/friction-log-landing-gear.md)
- [Discovery friction log (2026-03-09)](../feedback/2026-03-09/friction-log-discovery.md)
- [B-2 Spirit friction log (2026-03-08)](../feedback/2026-03-08/friction-log-b2-spirit.md)
- [Golden Gate Bridge friction log (2026-03-08)](../feedback/2026-03-08/friction-log-golden-gate-bridge.md)

| Milestone | Description | Size | Source |
|-----------|-------------|------|--------|
| M26 | Performance warnings in docs (twistExtrude, spline scaling) | XS | Helical gear #1 |
| M27 | Friendlier `cadtool init` when project exists | XS | Helical gear #6 |
| M28 | Better Python version / import error diagnostics | S | Helical gear (original) #1, #2 |
| M29 | Progress indicator for long `cadtool run` | M | Helical gear #3 |
| M30 | `involute_gear_profile` helper | M | Helical gear #2 |
| M31 | User-specified part colors in GLB export | S | Golden Gate Bridge, Landing Gear |
| M32 | `context` includes file paths per version | XS | Discovery log |
| M33 | Intermediate geometry preview / multi-shape show_object with labels | L | B-2 Spirit, Discovery, Landing Gear |
| M34 | Assembly positioning helpers (`bbox_point`, `place_at`, `assemble`) | S | Eiffel Tower |

---

## Milestone 26: Performance Warnings in Docs

**Source:** [Helical gear (latest)](../feedback/2026-03-10_15-56-10.md) friction point #1

**Goal:** Document the twistExtrude + spline performance cliff so agents don't burn 5-minute timeouts on complex profiles.

**Scope:**
- Add warning to `cadtool docs patterns`: "For twistExtrude with complex profiles (>100 points), prefer polyline over spline — the spline kernel scales poorly with point count."
- 1 test, 1 docs string edit.

**Status:** Pending

---

## Milestone 27: Friendlier `cadtool init` Error

**Source:** [Helical gear (latest)](../feedback/2026-03-10_15-56-10.md) friction point #6

**Goal:** When `cadtool init` is run in a directory that already has `cadtool.json`, return a helpful error that tells the agent what to do next.

**Scope:**
- Change error message to include: "Project already initialized. Run 'cadtool context' to see project state."
- 1 test update.

**Status:** Pending

---

## Milestone 28: Better Python Version / Import Error Diagnostics

**Source:** [Helical gear (original)](../feedback/2026-03-10/friction-log-helical-gear.md) friction points #1, #2

**Goal:** When CadQuery/OCP imports fail due to wrong Python version, tell the agent *why* instead of listing every OCP module as "not found."

**Scope:**
- In `validate.py` import check: if cadquery import fails AND `sys.version_info >= (3, 13)`, return a targeted message: "CadQuery requires Python 3.10-3.12. Current: {version}. Recreate venv with python3.12."
- Alternatively, check Python version in `cadtool init` or at CLI startup.
- ~3 tests.

**Status:** Pending

---

## Milestone 29: Progress Indicator for Long Runs

**Source:** [Helical gear (latest)](../feedback/2026-03-10_15-56-10.md) friction point #3, [B-2 Spirit](../feedback/2026-03-08/friction-log-b2-spirit.md)

**Goal:** When `cadtool run` takes >10 seconds, emit periodic progress to stderr so the agent (or human) knows it's working, not hung.

**Scope:**
- Option A: Background thread in `run.py` that writes `{"progress": "still running", "elapsed_s": N}` to stderr every 10 seconds during CQGI execution.
- Option B: Daemon sends heartbeat messages through the socket.
- JSON stdout remains clean (only final result). Progress goes to stderr only.
- ~3 tests.

**Status:** Pending

---

## Milestone 30: `involute_gear_profile` Helper

**Source:** [Helical gear (latest)](../feedback/2026-03-10_15-56-10.md) friction point #2

**Goal:** `involute_gear_profile(module, teeth, pressure_angle)` returns a closed TopoDS_Wire of an involute spur gear tooth profile. Eliminates ~80 lines of trig that agents currently write from scratch.

**Scope:**
- New function in `helpers.py`: involute curve generation, tooth tip/root arcs, full gear profile as closed wire.
- Add to preamble injection.
- Add to `cadtool docs helpers`.
- ~8 tests (tooth count, module scaling, pressure angle, wire closure, valid shape).

**Delivered:** 8 new tests. `involute_gear_profile(module, teeth, pressure_angle=20.0)` in `helpers.py` generates a closed `TopoDS_Wire` of an involute spur gear profile centered at origin in the XY plane. Involute curves sampled parametrically from the base circle, stitched with root arcs, tip arcs, and radial connectors into a closed wire. Handles `r_base > r_root` (radial segments down to dedendum) and `r_base <= r_root` (direct root arcs). Added to preamble, `cadtool docs helpers`, and `cadtool docs preamble`.

**Status:** Done

---

## Milestone 31: User-Specified Part Colors in GLB Export

**Source:** [Golden Gate Bridge](../feedback/2026-03-08/friction-log-golden-gate-bridge.md), [Landing Gear](../feedback/2026-03-09/friction-log-landing-gear.md)

**Goal:** Let scripts assign colors to parts so GLB exports are meaningful (e.g., "International Orange" for the Golden Gate Bridge, "black" for tires).

**Scope:**
- Extend `show_object()` or add a color argument: `show_object(part, color="orange")`.
- `export_glb()` uses user-specified colors when available, falls back to auto-palette.
- ~5 tests.

**Status:** Pending

---

## Milestone 32: File Paths in `cadtool context`

**Source:** [Discovery friction log](../feedback/2026-03-09/friction-log-discovery.md)

**Goal:** Each version in `cadtool context` output includes its file paths so the agent doesn't have to infer `v{N}_{label}/output.step` from convention.

**Scope:**
- Add `"outputs"` dict to each version entry in context JSON.
- 1-2 tests.

**Status:** Pending

---

## Milestone 33: Intermediate Geometry Preview

**Source:** [B-2 Spirit](../feedback/2026-03-08/friction-log-b2-spirit.md), [Discovery](../feedback/2026-03-09/friction-log-discovery.md), [Landing Gear](../feedback/2026-03-09/friction-log-landing-gear.md)

**Goal:** Let agents render intermediate construction geometry (individual wires, partial surfaces) during script development, not just the final result.

**Scope:** TBD — this is the biggest architectural question. Options:
- Named `show_object()` calls with selective rendering: `show_object(wire, name="root_section")`
- A `cadtool preview` command that renders any TopoDS_Shape from a script without requiring `show_object()`
- A debug mode that renders every `show_object()` call individually

**Status:** Pending (needs design)

---

## Milestone 34: Assembly Positioning Helpers

**Source:** [Eiffel Tower friction log](../feedback/2026-03-10_eiffel-tower-friction-log.md)

**Goal:** Eliminate manual coordinate math and type ceremony when positioning and assembling multi-part models.

**Delivered:** 14 new tests, 3 new functions in `helpers.py`:
- `bbox_point(shape, x, y, z)` — Query min/center/max point on a shape's bounding box. Returns (x, y, z) tuple.
- `place_at(shape, from_pt, to_pt)` — Translate shape so from_pt moves to to_pt. Declarative snap-to positioning.
- `assemble(*shapes)` — Combine TopoDS_Shape objects into a compound ready for show_object(). Eliminates Shape.cast/makeCompound/newObject ceremony.

All three added to preamble, `cadtool docs helpers`, and `cadtool docs patterns` (assembly positioning example).
