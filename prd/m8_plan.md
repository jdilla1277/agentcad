# M8: `cadtool render` — Standalone Render Command

## Context

After M7, agents can produce STEP + mesh exports + PNG renders — but only at `cadtool run` time. To get a new angle or zoom level, you'd have to re-run the entire script. M8 adds `cadtool render` so agents can render additional views of any existing STEP file without creating a new version.

`--focus` deferred to M9b to keep M8 scope focused on core rendering capabilities.

---

## CLI Interface

```bash
cadtool render v1_box/output.step --view iso
cadtool render v1_box/output.step --view front,top,iso
cadtool render v1_box/output.step --view all
cadtool render v1_box/output.step --view 45,30          # azimuth,elevation
cadtool render v1_box/output.step --view iso --zoom 2.0
cadtool render v1_box/output.step --view iso --name detail_view
```

## Custom Angle Convention

- **Azimuth** 0° = front (-Y eye position), increases counterclockwise from above
- **Elevation** 0° = horizontal, 90° = top-down
- Conversion to projection direction (eye→object):
  ```python
  az = math.radians(azimuth)
  el = math.radians(elevation)
  view.SetProj(-math.sin(az)*math.cos(el), math.cos(az)*math.cos(el), -math.sin(el))
  view.SetUp(0, 0, 1)  # Z-up
  ```

## View Parsing Disambiguation

`--view` value is parsed as:
1. If all comma-separated parts are known named views or "all" → multiple named views
2. If exactly 2 comma-separated parts, both numeric → azimuth,elevation
3. Otherwise → error

## Version Directory Detection

- If STEP parent dir matches `v\d+_\w+` and contains `meta.json` → save to `renders/`, update meta.json
- Otherwise → save PNG(s) next to the STEP file

---

## Files

### New (2)
```
app/src/cadtool/commands/render.py   ← render command
app/tests/test_render_cmd.py         ← CLI integration tests
```

### Modified (3)
```
app/src/cadtool/render.py            ← zoom param, custom angle, parse_view_spec(), refactor setup
app/src/cadtool/cli.py               ← register render command
app/tests/test_render.py             ← unit tests for zoom, custom angle, parse_view_spec
```

---

## Delivered

74 tests (53 existing + 8 unit + 13 integration), all passing. 3 commands: `init`, `run`, `render`.

### render.py changes
- `parse_view_spec(spec)` — parses view string into `[("named", name)]` or `[("custom", (az, el))]`
- `_setup_render(shape, width, height)` — extracted helper for offscreen GL pipeline
- `render_shape()` — added `zoom` parameter, calls `view.SetZoom(zoom)` after `FitAll()`
- `render_shape_custom(shape, azimuth, elevation, output_path)` — custom angle rendering

### commands/render.py
- STEP import via `cadquery.importers.importStep(path).val().wrapped`
- View parsing, routing to named vs custom render functions
- Version dir detection via regex + meta.json presence
- `--name` validates single-view only
- JSON output with `command`, `status`, `renders` keys
