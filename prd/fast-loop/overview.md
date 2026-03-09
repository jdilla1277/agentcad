# Fast Loop — Accelerating the Agent Iteration Cycle

## Problem Statement

The current agent workflow for building CAD geometry is:

```
write/edit script (~80 lines) → cadtool run (~30s) → read PNG → diagnose → repeat
```

For complex organic shapes (B-2 Spirit), this takes 6 iterations at ~30 seconds each. The total iteration cost scales with geometric complexity:

| Shape        | Iterations | Quality    |
|--------------|------------|------------|
| Pyramid      | 1          | Perfect    |
| Banana       | 4          | Workaround |
| B-2 Spirit   | 6          | Still rough |

The ~30 seconds breaks down into:

1. **Python cold start + CadQuery/OCP import** (~3-5s) — heavy native bindings loaded fresh every invocation
2. **Geometry building** (2-20s) — boolean ops, lofts, fillets
3. **STEP export** (~1-2s)
4. **PNG rendering** (~2-5s per view) — OCP viewer setup, render, save

But time-per-iteration is only half the problem. The other half is *wasted iterations* — builds that fail due to preventable mistakes (unclosed wires, missing `show_object()`, wrong OCP types) or that succeed but the agent can't tell without paying for a full render. Three of the B-2's six iterations were failures from opaque OCP errors.

### Evidence

Drawn from friction logs in `feedback/2026-03-08/` and `feedback/2026-03-09/`:

- **B-2 Spirit:** 6 iterations, 3 failures from `"BRep_API: command not done"` with zero diagnostic context. Each failure cost 30s of wall time for an error the agent couldn't prevent or learn from.
- **Banana:** 4 attempts to discover that CadQuery doesn't support tapered sweep natively, then 6 OCP classes to work around it.
- **Render journeys:** Agent couldn't distinguish "did my change work?" without a full render cycle. Many checks could have been answered with geometric metrics.
- **Discovery:** Agent read docs, tried non-existent features, wasted iterations on commands that didn't exist.

### Script Boilerplate Overhead

Agent-written scripts carry significant ceremony. Even the simplest useful script is:

```python
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10)
show_object(result)
```

One line of intent, two lines of boilerplate. For organic shapes requiring OCP escape hatches, scripts begin with 3-6 import lines:

```python
from OCP.gp import gp_Pnt, gp_Dir, gp_Ax2, gp_Circ
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeEdge
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
```

Every time the agent writes a script, it re-types these imports — and each line is a chance for a typo or wrong module path that wastes a full build cycle to discover.

---

## Design Principles

1. **Catch mistakes before they're expensive.** A check that runs in <100ms and prevents a 30-second failed build is worth more than making the build 5 seconds faster.
2. **Answer questions without pixels when possible.** Volume, bounding box, and face count are instant. Many "did it work?" checks don't need a render.
3. **Eliminate ceremony.** The agent should write design intent, not import statements. Anything the runtime can assume, it should.
4. **Make the fast path the default path.** The agent shouldn't need to opt into speed — the tool should be fast by default.

---

## Feature 1: Script Preamble (Implicit Runtime Context)

### What

The `cadtool run` script runtime pre-injects commonly needed modules into the script's namespace before execution. The agent writes only design intent.

### Pre-injected Names

| Name | Value | Why |
|------|-------|-----|
| `cq` | `cadquery` module | Used in every single script |
| `show_object` | CQGI's show_object | Already injected by CQGI, but document it |
| `helpers` | `cadtool.helpers` module | Avoids `from cadtool.helpers import ...` |
| `loft_sections` | `helpers.loft_sections` | Most common helper |
| `tapered_sweep` | `helpers.tapered_sweep` | Most common helper |
| `naca_wire` | `helpers.naca_wire` | Most common helper |
| `mirror_fuse` | `helpers.mirror_fuse` | Most common helper |

### Before

```python
import cadquery as cq
from cadtool.helpers import naca_wire, loft_sections, mirror_fuse

stations = [...]
sections = [naca_wire(...) for s in stations]
body = loft_sections(sections, smooth=True)
result = mirror_fuse(body, plane="XZ")
show_object(result)
```

### After

```python
stations = [...]
sections = [naca_wire(...) for s in stations]
body = loft_sections(sections, smooth=True)
result = mirror_fuse(body, plane="XZ")
show_object(result)
```

Three import lines eliminated. The script is pure design intent.

### Explicit imports still work

Scripts that include `import cadquery as cq` continue to work — the preamble doesn't conflict. This keeps existing scripts compatible and lets agents bring in non-standard modules when needed.

### Docs integration

`cadtool docs` should describe the preamble so agents know what's available without guessing. The docs section should list every pre-injected name and its purpose.

---

## Feature 2: Pre-Execution Validation

### What

Before building geometry (the expensive step), `cadtool run` performs fast static and lightweight runtime checks on the script. Failures are returned immediately (<100ms) with actionable error messages, without consuming a version number.

### Check Categories

**Static checks (no execution):**

| Check | What it catches | Error message |
|-------|----------------|---------------|
| `show_object()` call present | Scripts that build geometry but never surface it | `"Script does not call show_object(). Add show_object(result) to surface your geometry."` |
| Syntax validation | Typos, indentation errors | Python's native `SyntaxError` with line number |
| Import resolution | Misspelled module names | `"Import error: 'OCP.gp.gp_Pntt' — did you mean 'gp_Pnt'?"` |

**Lightweight runtime checks (fast execution, before full build):**

| Check | What it catches | Error message |
|-------|----------------|---------------|
| Wire closure | Unclosed wires that will cause loft/sweep failures | `"Wire is not closed: gap of {distance}mm between start and end points."` |
| Parameter range | Fillet radius larger than edge length, zero-thickness bodies | `"Fillet radius {r} exceeds minimum edge length {e}."` |

### Validation output

Validation errors should be returned with `status: "validation_error"` (distinct from `status: "failed"` which means the build was attempted and crashed). Validation errors do NOT consume a version number — the build never started.

```json
{
  "command": "run",
  "status": "validation_error",
  "checks": [
    {
      "check": "show_object_missing",
      "severity": "error",
      "message": "Script does not call show_object(). Add show_object(result) to surface your geometry."
    }
  ]
}
```

### Non-blocking warnings

Some checks should warn without blocking:

```json
{
  "checks": [
    {
      "check": "high_face_count",
      "severity": "warning",
      "message": "Script produced 2,847 faces. Consider simplifying geometry for faster iteration."
    }
  ]
}
```

Warnings are returned alongside successful build results.

---

## Feature 3: Geometric Metrics in Build Output

### What

Every successful `cadtool run` returns geometric metrics alongside the existing output. The agent can answer "did it work?" and "did my change do what I expected?" without waiting for a render.

### Metrics

| Metric | Type | Use case |
|--------|------|----------|
| `bounding_box` | `{x: [min, max], y: [min, max], z: [min, max]}` | "Is the shape the right size?" |
| `dimensions` | `{x: float, y: float, z: float}` | Shorthand for bbox extent |
| `volume` | `float` (mm^3) | "Did my boolean subtract too much?" |
| `surface_area` | `float` (mm^2) | General sanity check |
| `center_of_mass` | `{x, y, z}` | Symmetry verification |
| `face_count` | `int` | Complexity indicator |
| `edge_count` | `int` | Complexity indicator |
| `is_valid` | `bool` | OCP shape validity check |
| `is_watertight` | `bool` | Solid body verification |

### Output format

Metrics are included in the run output under a `metrics` key:

```json
{
  "command": "run",
  "status": "success",
  "version": 3,
  "label": "fuselage",
  "outputs": { "step": "v3_fuselage/output.step" },
  "metrics": {
    "bounding_box": {"x": [-25.5, 25.5], "y": [0.0, 12.0], "z": [-1.8, 1.8]},
    "dimensions": {"x": 51.0, "y": 12.0, "z": 3.6},
    "volume": 487.3,
    "surface_area": 1204.1,
    "center_of_mass": {"x": 0.0, "y": 5.2, "z": 0.0},
    "face_count": 14,
    "edge_count": 26,
    "is_valid": true,
    "is_watertight": true
  }
}
```

### Metrics also written to meta.json

The same metrics block is persisted in the version's `meta.json` so that `cadtool diff` can compare metrics across versions (e.g., "volume changed by +12%").

### Implementation notes

All metrics are cheap to compute from a `TopoDS_Shape`:
- Bounding box: `Bnd_Box` + `BRepBndLib.Add_s()`
- Volume/surface area: `GProp_GProps` + `BRepGProp.VolumeProperties_s()` / `SurfaceProperties_s()`
- Center of mass: from `GProp_GProps.CentreOfMass()`
- Face/edge counts: `TopExp_Explorer` iteration
- Validity: `BRepCheck_Analyzer`
- Watertight: `ShapeAnalysis_ShapeContents` or check that all edges are shared by exactly 2 faces

---

## Feature 4: Persistent Worker (Daemon Mode)

### What

A long-running background process keeps CadQuery and OCP loaded in memory. Script execution goes through the daemon, eliminating the 3-5 second cold start on every invocation.

### Interface

```bash
cadtool daemon start          # Start the background worker
cadtool daemon stop           # Stop it
cadtool daemon status         # Check if running

cadtool run script.py --output label   # Unchanged — auto-routes to daemon if running
```

The agent's workflow doesn't change. `cadtool run` detects a running daemon and routes the script to it. If no daemon is running, it falls back to the current direct-execution path.

### Architecture

```
cadtool run ──→ daemon (if running) ──→ execute script ──→ JSON response
              └→ direct execution (fallback)
```

The daemon:
- Listens on a Unix domain socket (no network exposure)
- Pre-imports `cadquery`, `OCP`, and `cadtool.helpers`
- Executes scripts in isolated namespaces (no state leakage between runs)
- Returns JSON results over the socket
- Auto-exits after configurable idle timeout (default: 30 minutes)

### What it buys

| Without daemon | With daemon |
|---------------|-------------|
| ~30s per iteration | ~25s per iteration |
| Cold start every run | Cold start once |

The 3-5 second savings compounds across iterations. For a 6-iteration B-2 session, that's 18-30 seconds saved — roughly one free extra iteration.

### Risk: State leakage

Scripts should not be able to affect subsequent runs. The daemon must reset the execution namespace between invocations. CQGI already provides script isolation via `ScriptCallback.build()`, which is a good foundation.

---

## Feature 5: Quick Preview Mode

### What

A `--preview` flag on `cadtool run` that produces a small, fast render for shape verification during iteration. Full-quality renders are reserved for final output.

### Parameters

| | Preview | Full |
|---|---------|------|
| Resolution | 256x256 | 1024x1024 |
| Anti-aliasing | Off | On |
| Views | iso only | Configurable |
| Purpose | "Did my shape change in the right direction?" | Final documentation |

### Usage

```bash
cadtool run script.py --output wing_v2 --preview
```

Output includes a single `preview` path instead of named renders:

```json
{
  "command": "run",
  "status": "success",
  "preview": "v2_wing_v2/preview.png",
  "metrics": { ... }
}
```

Preview images are not tracked in the version's render history — they're ephemeral working artifacts.

### Interaction with --render

`--preview` and `--render` can coexist. `--preview` produces the fast iso preview; `--render` produces the full-quality views. The agent uses `--preview` during iteration and adds `--render` on the final run.

---

## Feature 6: Parametric Scripts

### What

CQGI already supports script parameters via a `describe_parameters()` convention. `cadtool run` exposes this with a `--params` flag so agents can adjust values without rewriting scripts.

### Usage

Script declares parameters:

```python
length = 50.0    # cadquery: param
sweep = 0.3      # cadquery: param
stations = 9     # cadquery: param

# ... design code using length, sweep, stations ...
show_object(result)
```

Agent adjusts parameters without touching the script:

```bash
cadtool run design.py --output v1 --params length=60,sweep=0.4
cadtool run design.py --output v2 --params length=60,sweep=0.2
cadtool run design.py --output v3 --params length=45,sweep=0.3
```

### What it buys

- Agent iterates by changing numbers, not rewriting code
- No risk of syntax errors, broken imports, or restructured logic
- Enables parallel variant exploration (3 runs with different params)

### Output

The params used are recorded in meta.json and the run output:

```json
{
  "command": "run",
  "status": "success",
  "params": {"length": 60.0, "sweep": 0.4},
  "metrics": { ... }
}
```

---

## Phasing

### Phase 1: Cheap Wins (reduce wasted iterations)

| Feature | Effort | Impact |
|---------|--------|--------|
| Pre-execution validation | Small | High — prevents 30s wasted builds |
| Geometric metrics in output | Small | High — answers "did it work?" without render |
| Script preamble | Small | Medium — eliminates boilerplate mistakes |

These three features share a theme: make the *existing* loop smarter without changing its architecture. They can ship together in a single milestone.

**Expected impact:** Fewer wasted iterations (the B-2's 3 failure iterations could have been caught in <100ms each), and the agent can verify gross shape correctness from metrics alone for many iterations.

### Phase 2: Faster Loop

| Feature | Effort | Impact |
|---------|--------|--------|
| Quick preview mode | Small | Medium — faster visual checks |
| Persistent worker | Medium | Medium — 3-5s off every run |
| Parametric scripts | Small | Medium — safer iteration on values |

These change the speed of each iteration. The daemon is the most complex but enables further optimizations (cached shapes, parallel builds).

**Expected impact:** Per-iteration time drops from ~30s to ~22s. Parametric iteration further reduces time for parameter-tuning phases.

### Deferred

| Feature | Why defer |
|---------|-----------|
| Incremental/checkpoint builds | High complexity, uncertain reliability |
| Shape diff visualization | Valuable but complex; geometric metrics cover most cases |
| Section analysis (`cadtool section`) | Useful for organic shapes but niche |
| Parallel variant exploration | Valuable but requires daemon + parametric as prerequisites |

---

## Success Criteria

1. **A script that's missing `show_object()` is caught in <100ms** without consuming a version number.
2. **The agent can determine if a shape is the right size and valid** from `cadtool run` output alone, without rendering.
3. **A simple CadQuery script requires zero import lines** — just design code and `show_object()`.
4. **Iteration time for a cached daemon drops below 25 seconds** for a moderately complex shape.
5. **The B-2 Spirit scenario** (complex organic surface, 6 iterations) can be completed with fewer failed iterations and faster per-iteration time.
