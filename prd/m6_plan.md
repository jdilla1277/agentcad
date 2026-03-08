# M6 — PNG Rendering

## Goal

Add offscreen PNG rendering to `cadtool run` so agents get visual feedback directly, without needing an external STEP viewer.

## Implementation

### Rendering approach

Uses OCP direct rendering (`V3d_View.ToPixMap`) — zero new dependencies, works headlessly via software renderer (`contextNoAccel = True`, `buffersNoSwap = True`), operates directly on `TopoDS_Shape` from CQGI results.

### CLI interface

`--render` option added to `cadtool run`:

```bash
cadtool run script.py --output label --render iso       # single view
cadtool run script.py --output label --render front,iso  # multiple views
cadtool run script.py --output label --render all        # front, right, top, iso
```

Available views: `front`, `back`, `left`, `right`, `top`, `bottom`, `iso`

`all` expands to: `front`, `right`, `top`, `iso`

### Directory structure

```
v1_label/
  script.py
  output.step
  meta.json
  renders/          ← only when --render is used
    iso.png
    front.png
```

### JSON output

When `--render` is used, both meta.json and CLI output include a `renders` dict mapping view names to relative paths. When `--render` is omitted, the `renders` key is absent entirely (backward compatible).

### Failed runs

No renders on failure — `_record_failure()` is unchanged.

## Files

### New
- `app/src/cadtool/render.py` — OCP offscreen rendering module
- `app/tests/test_render.py` — 7 unit tests for render module
- `prd/m6_plan.md` — this document

### Modified
- `app/src/cadtool/commands/run.py` — `--render` option wired in
- `app/tests/test_run.py` — 6 integration tests for `--render`

## Test count

43 total (30 existing + 13 new), all passing.
