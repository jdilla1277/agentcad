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
| M6 | PNG rendering | **Next** |
| M7 | GLB/OBJ export | Planned |
| M8 | `cadtool render` (custom views) | Planned |
| ~~M9~~ | ~~2D primitive cleanup~~ | Done (absorbed into M4) |
| M10 | `cadtool context` & `cadtool docs` | Planned |
| M11 | Polish & distribution | Planned |

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

## Milestone 6: PNG Rendering

**Goal:** Produce PNG renders from STEP files at standard angles.

### Tasks
1. Integrate pythonOCC offscreen rendering (macOS)
2. Implement standard views: front, side, top, iso
3. `--formats` flag parsing: `png` = all four views, `png-front` = single view
4. Renders saved to `vN/renders/` directory
5. Render paths recorded in output JSON and `meta.json`

### Tests
- `cadtool run script.py --output v1 --formats step,png` produces 4 PNGs
- `--formats step,png-iso` produces only iso render
- Render files are valid PNGs with non-zero size
- Output JSON lists correct render paths
- Default formats (`step,png`) work when `--formats` is omitted

---

## Milestone 7: GLB & OBJ Export

**Goal:** Tessellated mesh exports for human review and interop.

### Tasks
1. GLB export via pythonOCC tessellation
2. OBJ export via pythonOCC tessellation
3. Wire into `--formats` flag: `glb`, `obj`
4. Record in output JSON

### Tests
- `--formats step,glb` produces valid GLB file
- `--formats step,obj` produces valid OBJ file
- GLB opens correctly (validated by file header/magic bytes)
- Output JSON includes correct paths

---

## Milestone 8: `cadtool render`

**Goal:** Render additional views of an existing version without creating a new version.

### Tasks
1. `cadtool render <step_path> --view <angle>`
2. Standard views: `iso`, `front`, `side`, `top`
3. Custom angle: `--view "45,30"`
4. `--zoom` flag
5. `--focus` flag for camera target
6. `--name` flag for named renders
7. Named renders saved to version's `renders/` directory and recorded in `meta.json`

### Tests
- Rendering an existing STEP file produces a PNG
- Custom angle views produce correct output
- `--name` flag saves render with custom filename
- `meta.json` is updated with new render entry
- `--zoom` and `--focus` affect the output (file differs from default)

---

## ~~Milestone 9: 2D Primitive Cleanup~~ ✓ (absorbed into M4)

Completed as part of M4. Removed `add-rect`, `add-circle`, `list`, `delete`, `get` commands, `objects`/`next_id` from manifest, and all associated tests.

---

## Milestone 10: `cadtool context` & `cadtool docs`

**Goal:** Agent discoverability commands.

### Tasks
1. `cadtool context` returns project state summary (current version, version count, project name)
2. `cadtool context --json` returns structured JSON
3. `cadtool docs` returns full markdown documentation
4. `cadtool docs <section>` returns specific section (e.g., `render`, `schema`)

### Tests
- `cadtool context` in an initialized project returns project summary
- `cadtool context --json` returns valid JSON with project state
- `cadtool context` outside a project returns helpful error
- `cadtool docs` returns non-empty markdown
- `cadtool docs render` returns render-specific docs
- `cadtool docs schema` returns the output schema

---

## Milestone 11: Polish & Distribution

**Goal:** Installable, documented, ready for real use.

### Tasks
1. `--help` on all commands with clear descriptions
2. Input validation and helpful error messages across all commands
3. `pip install git+https://github.com/...` works cleanly
4. End-to-end test: init → run → render → context (full workflow)
5. Verify all JSON output matches documented schema

### Tests
- End-to-end workflow test passes
- `--help` output exists for all commands
- Install from git produces a working `cadtool` command
- Invalid inputs return helpful JSON errors (not stack traces)

---

## Dependency Graph

```
M1 (init) → M4 (run/STEP) → M5 (errors) → M6 (PNG) → M7 (GLB/OBJ) → M8 (render)
                                                                           ↓
M1 ──────────────────────────────────────────────────────────→ M10 (context/docs)
                                                                           ↓
                                                                     M11 (polish)
```

M1 is the foundation. M4 delivered the core 3D pipeline (and absorbed M9 cleanup). M5-M8 build out the pipeline. M10-M11 polish for distribution. M2-M3 (2D primitives) were scaffolding milestones — code has been removed.
