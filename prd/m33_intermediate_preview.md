# M33: Per-Part Design Workflow

## Problem

Agents are blind until script execution completes. The current workflow is:

1. Write/modify 80+ lines of construction code
2. Wait 5-30 seconds for `cadtool run` + render
3. Get a single static PNG of the **final compound**
4. Try to diagnose what went wrong
5. Repeat

This kills iteration velocity for organic modeling (B-2 Spirit: 6 iterations, most spent diagnosing issues visible only in renders) and multi-part assembly (Landing Gear: 7 parts, positioning errors cascade with no per-part feedback).

**Root cause:** `run.py` discards all `show_object()` metadata (name, color, options) and blindly auto-compounds everything into a single shape. The per-part identity and agent intent are lost.

## Key Insight

CQGI already supports rich metadata on `show_object()`:

```python
show_object(base, name="deck", color="blue")
show_object(pin, name="guide_pin", color="red")
```

Each call produces a `ShapeResult` with `.shape` and `.options` dict. cadtool currently ignores `.options` entirely. If we stop discarding this metadata, agents can design parts individually with focused feedback — no new flags needed.

## Agent Experience

### Building a bridge: part-by-part design

**Step 1: Agent starts with the deck.**

```python
# bridge.py
deck = cq.Workplane("XY").box(200, 40, 3)
show_object(deck, name="deck", color="gray")
```

```bash
$ cadtool run bridge.py --output bridge --preview
```

```json
{
  "command": "run",
  "status": "success",
  "version": 1,
  "parts": [
    {
      "name": "deck",
      "color": "gray",
      "metrics": {"volume": 24000, "dims": [200, 40, 3], ...},
      "preview": "v1_bridge/parts/deck_preview.png"
    }
  ],
  "metrics": {"volume": 24000, "dims": [200, 40, 3], ...},
  "preview": "v1_bridge/preview.png"
}
```

Agent sees one part, one preview. Compound preview and part preview are identical (only one part). Agent checks dimensions, decides the deck looks good.

**Step 2: Agent adds towers.**

```python
# bridge.py
deck = cq.Workplane("XY").box(200, 40, 3)
tower_l = cq.Workplane("XY").center(-60, 0).box(5, 5, 80)
tower_r = cq.Workplane("XY").center(60, 0).box(5, 5, 80)

show_object(deck, name="deck", color="gray")
show_object(tower_l, name="tower_left", color="orange")
show_object(tower_r, name="tower_right", color="orange")
```

```bash
$ cadtool run bridge.py --output bridge --preview
```

```json
{
  "command": "run",
  "status": "success",
  "version": 2,
  "parts": [
    {
      "name": "deck",
      "color": "gray",
      "metrics": {"volume": 24000, "dims": [200, 40, 3], ...},
      "preview": "v2_bridge/parts/deck_preview.png"
    },
    {
      "name": "tower_left",
      "color": "orange",
      "metrics": {"volume": 2000, "dims": [5, 5, 80], ...},
      "preview": "v2_bridge/parts/tower_left_preview.png"
    },
    {
      "name": "tower_right",
      "color": "orange",
      "metrics": {"volume": 2000, "dims": [5, 5, 80], ...},
      "preview": "v2_bridge/parts/tower_right_preview.png"
    }
  ],
  "metrics": {"volume": 28000, ...},
  "preview": "v2_bridge/preview.png"
}
```

Now the agent has:
- **Compound preview** (`preview.png`) — the whole bridge so far, parts colored gray and orange
- **Per-part previews** — each tower in isolation, the deck in isolation
- **Per-part metrics** — "tower_left volume is 2000, dims are 5x5x80" — agent can verify each part independently

Agent looks at tower_left_preview.png: "The tower is sitting at the origin, not on top of the deck. I need to translate it up by 3." It fixes just the tower positioning without re-examining the whole model.

**Step 3: Agent focuses on cables.**

```python
# bridge.py  (deck and towers unchanged)
# ...
cable_points = [(x, 0, cable_height(x)) for x in range(-60, 61, 10)]
cable = cq.Workplane("XZ").spline(cable_points).wire()
cable_solid = cq.Workplane("XZ").sweep(cable, cq.Workplane("XZ").circle(0.5))

show_object(deck, name="deck", color="gray")
show_object(tower_l, name="tower_left", color="orange")
show_object(tower_r, name="tower_right", color="orange")
show_object(cable_solid, name="main_cable", color="red")
```

```bash
$ cadtool run bridge.py --output bridge --preview
```

Agent checks `main_cable` preview in isolation — sees just the cable shape without deck/towers obscuring it. Checks the metrics: "cable volume is 450, that seems thin — I'll increase the radius to 1.0."

The agent iterates on the cable while the deck and towers are stable. Each run gives focused feedback on the part being designed.

**Step 4: Agent views the assembly.**

```bash
$ cadtool view v3_bridge/output.step
```

Opens browser — all parts visible with their specified colors (gray deck, orange towers, red cables). Agent orbits and inspects the assembly. The GLB uses agent-specified colors, not random palette colors.

### Render for detailed inspection

At any point the agent can get more views of specific parts:

```bash
$ cadtool run bridge.py --output bridge --render front,right --preview
```

Per-part renders go to `v4_bridge/parts/`:
```
v4_bridge/
  output.step
  preview.png                      # compound iso
  renders/
    front.png                      # compound front
    right.png                      # compound right
  parts/
    deck_preview.png               # deck iso
    deck_front.png                 # deck front
    deck_right.png                 # deck right
    tower_left_preview.png         # tower iso
    tower_left_front.png
    tower_left_right.png
    main_cable_preview.png
    main_cable_front.png
    main_cable_right.png
```

### Unnamed parts — backward compatible

Scripts that don't use names behave exactly as today:

```python
box = cq.Workplane("XY").box(10, 10, 10)
show_object(box)
```

No `parts` array in the output. No per-part renders. Single preview. Identical to current behavior.

Multiple unnamed `show_object()` calls get auto-indexed names (`part_1`, `part_2`) and the existing auto-compound warning:

```python
show_object(box)
show_object(cylinder)
```

```json
{
  "parts": [
    {"name": "part_1", "metrics": {...}, "preview": "..."},
    {"name": "part_2", "metrics": {...}, "preview": "..."}
  ],
  "warning": "2 show_object() calls detected, results combined into a single compound."
}
```

## Implementation Phases

### Phase 1: Per-part identity and metrics (S)

**Stop discarding metadata.** Foundation for everything else.

1. **Preserve per-object metadata in run.py** — extract `name` and `color` from `ShapeResult.options`
2. **Per-part metrics** — `compute_metrics()` for each part individually
3. **Per-part colors in GLB** — use agent-specified colors when available, fall back to auto-palette (subsumes M31)
4. **Output JSON** — `"parts"` array with name, color, metrics
5. **meta.json** — same parts data persisted

### Phase 2: Per-part rendering (S)

1. **Per-part previews** — `--preview` renders each named part in isolation to `parts/` subdirectory
2. **Per-part renders** — `--render` renders each named part with requested views
3. **Compound renders** — unchanged, still produced as today
4. **Output JSON** — preview/render paths under each part entry

### Phase 3: Construction stages (M — stretch, needs design)

`show_debug(shape, name="stage")` for intermediate geometry that shouldn't appear in the final output. Only rendered when `--debug` flag is passed. Useful for inspecting wires before lofting, surfaces before booleans, etc. Injected via preamble.

## What This Subsumes

- **M31 (user-specified part colors)** — Phase 1 reads color from show_object options
- **M33 as originally scoped** — Phase 1 + 2 cover the core need

## Design Decisions

1. **Naming** — no name on a single show_object = today's behavior (no `parts`). Multiple unnamed calls get `part_1`, `part_2`. Named parts opt into per-part features.
2. **Per-part renders** — always iso for `--preview`, requested views for `--render`. No separate flag needed. Naming your parts opts you in.
3. **show_debug injection (Phase 3)** — inject via preamble. Appends to a separate list, not `outputObjects`. Results never in STEP/compound, only rendered with `--debug`.
4. **Backward compatibility** — scripts without names behave identically to today.
5. **File layout** — per-part renders go in `parts/` subdirectory to keep the version directory clean.
