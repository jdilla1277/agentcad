# Friction Log — Journey Testing (2026-03-07)

**Tester:** Claude Code (AI agent)
**Scope:** 5 user journeys exercising init, add-rect, add-circle, list
**Overall verdict:** Surprisingly solid for early-stage. All 5 journeys completed successfully. Friction is minor and mostly about consistency.

---

## Journey 1: Hello World

```
cadtool init --name "my-first-drawing"  → success
cadtool add-rect --x 0 --y 0 --width 200 --height 100  → success, id 1
cadtool list  → success, shows 1 rect
```

**Result: Clean pass.** No friction. Init, add, list all work exactly as described. JSON output is parseable, IDs are assigned, and the shape data round-trips correctly through the manifest.

---

## Journey 2: Mixed Shapes

```
cadtool init --name "logo"  → success
cadtool add-rect --x 0 --y 0 --width 300 --height 300  → id 1
cadtool add-circle --cx 150 --cy 150 --radius 100  → id 2
cadtool add-circle --cx 150 --cy 150 --radius 50  → id 3
cadtool list  → 3 objects, correct types and params
```

**Result: Clean pass.** IDs sequence correctly (1, 2, 3). Mixed shape types coexist in the manifest without issue. Circle uses `cx`/`cy` while rect uses `x`/`y` — this is a reasonable convention but worth noting.

### Minor friction: No echo of shape params on add

When `add-rect` returns `{"command": "add-rect", "status": "success", "id": 1}`, it doesn't echo back the shape parameters. An agent has to call `list` or re-read the manifest to confirm the shape was created with the right values. Returning the full object in the response would save a round trip:

```json
{"command": "add-rect", "status": "success", "id": 1, "object": {"type": "rect", "x": 0.0, "y": 0.0, "width": 300.0, "height": 300.0}}
```

**Severity:** Low. `list` is cheap. But for an agent building iteratively, every saved round trip matters.

---

## Journey 3: Stress the ID System

### Phase 1: Sequential IDs

```
cadtool init --name "id-test"
add-rect x3 → ids 1, 2, 3
```

Works correctly. IDs are sequential starting from 1.

### Phase 2: ID gap after manual deletion

Manually edited `cadtool.json` to remove the object with id 2. Then:

```
cadtool add-circle --cx 0 --cy 0 --radius 5  → id 4
```

**Correct behavior.** New shape gets id 4 (max existing id 3 + 1), not id 2 (gap-filling). This is the right design — IDs are stable references, not array indices. An agent or human that recorded "id 2 is the middle rect" won't have that reference silently reassigned to a circle.

### Minor friction: No warning about gaps

The tool doesn't warn that there's a gap in the ID sequence. This is probably fine — warnings for valid states add noise. But it's worth a deliberate decision: should `list` output include a `count` field so the agent can detect gaps without doing arithmetic?

**Severity:** Very low. Not actionable now.

---

## Journey 4: Error Recovery

This was the most interesting journey. Tested 4 error cases:

### 4a. add-rect before init

```
cadtool add-rect --x 0 --y 0 --width 10 --height 10
→ {"status": "error", "message": "cadtool.json not found. Run 'cadtool init' first."}
EXIT: 1
```

**Friction: Missing `command` key.** Every other error response includes `"command": "add-rect"` or `"command": "add-circle"`. This one only has `status` and `message`. An agent parsing responses with a consistent schema (`response["command"]`) will crash here.

This is likely because the "no manifest" check runs before the command-specific logic and uses a different error path.

**Severity: Medium.** Schema inconsistency in error responses is exactly the kind of thing that breaks agent loops. Should be `{"command": "add-rect", "status": "error", "message": "..."}`.

### 4b. Negative width

```
cadtool add-rect --x 0 --y 0 --width -10 --height 5
→ {"command": "add-rect", "status": "error", "message": "width must be greater than 0"}
EXIT: 1
```

**Clean.** Good error message, correct exit code, valid JSON.

### 4c. Zero radius

```
cadtool add-circle --cx 0 --cy 0 --radius 0
→ {"command": "add-circle", "status": "error", "message": "radius must be greater than 0"}
EXIT: 1
```

**Clean.** Same pattern as above.

### 4d. Recovery — valid command after errors

```
cadtool init --name "oops"  → success
cadtool add-rect --x 0 --y 0 --width 10 --height 10  → success, id 1
cadtool list  → 1 object
```

**Clean pass.** Errors don't corrupt state. The manifest isn't written until validation passes. This is correct and important.

### Additional edge cases tested

```
cadtool add-rect --x 0 --y 0 --width 0 --height 10  → error (width must be > 0)
cadtool add-rect --x 0 --y 0 --width 10 --height -5  → error (height must be > 0)
cadtool add-circle --cx 0 --cy 0 --radius -1  → error (radius must be > 0)
```

All correct. Zero and negative values are both rejected. Validation is consistent across shape types.

---

## Journey 5: Build Something Real (Smiley Face)

```
cadtool init --name "smiley-face"
cadtool add-circle --cx 150 --cy 150 --radius 120    → id 1 (head)
cadtool add-circle --cx 110 --cy 110 --radius 15     → id 2 (left eye)
cadtool add-circle --cx 190 --cy 110 --radius 15     → id 3 (right eye)
cadtool add-rect --x 143 --y 135 --width 14 --height 25  → id 4 (nose)
cadtool add-rect --x 110 --y 190 --width 80 --height 12  → id 5 (mouth)
```

Final list confirms all 5 shapes with correct coordinates.

**Result: It works.** I was able to reason spatially and place shapes relative to each other using coordinates. The tool doesn't fight you — it stays out of the way and records what you tell it.

### Friction: No way to verify spatial relationships

After placing 5 shapes, I have to trust my coordinate math. There's no way to ask "does the left eye overlap with the head?" or "is the mouth centered?" The `list` command dumps raw coordinates but provides no spatial context.

This is expected for the current scope — the PRD's render commands (Milestone 4+) would solve this. But for the immediate 2D shape tool, a `cadtool export-svg` or even a basic ASCII render would be transformative for agent usability. An agent that can *see* what it built can self-correct. Without visual feedback, the agent is doing spatial reasoning blind.

**Severity: Medium-high for agent workflows, but acknowledged as future scope.** Worth flagging because the journey prompt ("can Claude reason spatially with the tools it has?") surfaces this as the biggest gap.

### Friction: No labels or names for shapes

Every shape is identified only by numeric ID. When building the face, I'm mentally tracking "id 1 = head, id 2 = left eye..." but there's no way to record that intent in the tool. A `--name` or `--label` flag would help both agents and humans reason about drawings:

```
cadtool add-circle --cx 150 --cy 150 --radius 120 --name "head"
```

**Severity:** Low-medium. Not critical for correctness, but high value for agent usability.

---

## Summary of Findings

### What's working well

1. **JSON contract is consistent** (with one exception — see below)
2. **Validation catches bad input** — negative, zero values all rejected with clear messages
3. **Error recovery is clean** — bad commands don't corrupt the manifest
4. **ID assignment is correct** — max(ids) + 1, no gap-filling, stable references
5. **Mixed shape types work** — rect and circle coexist cleanly
6. **The tool stays out of the way** — no unnecessary prompts, confirmations, or verbose output

### Issues to fix

| # | Issue | Severity | Fix effort |
|---|-------|----------|------------|
| 1 | "No manifest" error missing `command` key | Medium | Small — add command name to the pre-check error path |
| 2 | `add-*` responses don't echo back shape params | Low | Small — include the full object in success response |

### Features that would most improve agent usability

| # | Feature | Impact |
|---|---------|--------|
| 1 | Visual output (SVG export or ASCII preview) | High — agents can't self-correct without seeing the drawing |
| 2 | Shape labels (`--name` flag) | Medium — lets agents track intent, not just IDs |
| 3 | `count` field in `list` response | Low — convenience for detecting gaps/validating state |
