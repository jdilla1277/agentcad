# M7 — GLB & STL Export

## Context

After M6, agents can run CadQuery scripts and get versioned STEP files plus PNG renders. But STEP is a CAD-native format — agents and humans who want to preview geometry in a web viewer, send to a slicer, or embed in a 3D scene need mesh formats. M7 adds GLB and STL export via a new `--export` flag on `cadtool run`.

OBJ export is deferred to a future milestone. There's no built-in OCP writer for OBJ (it requires manual triangle extraction with per-face index offsetting), and GLB covers the same use cases with a better format (binary, smaller, widely supported). OBJ will be added later for completeness since agents should be able to produce any format they'd reasonably need.

## Branch

`m7-glb-stl-export` off `main`

---

## Design

### Export approach

- **STL** — CadQuery's `exporters.export(shape, path, exportType="STL")` handles this directly. Zero new code beyond wiring it in.
- **GLB** — OCP's `RWGltf_CafWriter` writes binary glTF. Requires tessellating the shape with `BRepMesh_IncrementalMesh`, wrapping it in an XCAF document (`TDocStd_Document`), then calling the writer. New `export_glb()` function in a dedicated module.

STEP is always produced (it's the source of truth). `--export` adds mesh formats alongside it.

### CLI interface

Add `--export` option to `cadtool run`:

```bash
# No export (default, backward compatible)
cadtool run script.py --output label

# STL only
cadtool run script.py --output label --export stl

# GLB only
cadtool run script.py --output label --export glb

# Both mesh formats
cadtool run script.py --output label --export stl,glb

# Combine with render
cadtool run script.py --output label --export glb --render iso
```

### Directory structure

```
v1_label/
  script.py
  output.step          ← always
  output.stl           ← if --export stl
  output.glb           ← if --export glb
  meta.json
  renders/             ← if --render
```

### Updated meta.json (with exports)

```json
{
  "version": 1,
  "label": "label",
  "status": "success",
  "created": "2026-03-08T...",
  "script": "v1_label/script.py",
  "outputs": {
    "step": "v1_label/output.step",
    "stl": "v1_label/output.stl",
    "glb": "v1_label/output.glb"
  }
}
```

STL and GLB paths appear in `outputs` alongside `step` — they're all geometry outputs, not separate like `renders`.

### Updated JSON response (with exports)

```json
{
  "command": "run",
  "status": "success",
  "version": 1,
  "label": "label",
  "outputs": {
    "step": "v1_label/output.step",
    "stl": "v1_label/output.stl",
    "glb": "v1_label/output.glb",
    "script": "v1_label/script.py"
  }
}
```

When `--export` is not used, only `step` and `script` appear in `outputs` (backward compatible).

### Failed runs

No exports produced on failure — `_record_failure()` path is unchanged.

---

## Files

### New (2)
```
app/src/cadtool/export.py      ← GLB export function (STL uses CadQuery exporters directly)
app/tests/test_export.py       ← unit tests for export module
```

### Modified (2)
```
app/src/cadtool/commands/run.py    ← --export flag, wire in export calls
app/tests/test_run.py              ← integration tests for --export on run
```

### New (1)
```
prd/m7_plan.md                     ← this plan as PRD doc
```

---

## TDD Phases

### Phase 1: Export module — RED then GREEN

Create `app/tests/test_export.py` with unit tests for `export_glb()`:

- `test_export_glb_produces_file` — call `export_glb(shape, output_path)`, assert file exists with non-zero size
- `test_export_glb_valid_magic_bytes` — GLB files start with `glTF` (bytes `67 6C 54 46`)
- `test_export_glb_different_shapes_differ` — a box and a cylinder produce different GLB files

Then create `app/src/cadtool/export.py`:
- `export_glb(shape, output_path, linear_deflection=0.1)` — tessellates shape, creates XCAF doc, writes GLB via `RWGltf_CafWriter`

STL export uses `cadquery.exporters.export()` directly in `run.py` — no wrapper needed.

### Phase 2: CLI integration tests — RED then GREEN

Add to `app/tests/test_run.py`:

- `test_run_with_export_stl` — `--export stl` creates `v1_label/output.stl` with non-zero size
- `test_run_with_export_glb` — `--export glb` creates `v1_label/output.glb` with valid magic bytes
- `test_run_with_export_multiple` — `--export stl,glb` creates both files
- `test_run_with_export_and_render` — `--export glb --render iso` produces both GLB and PNG
- `test_run_with_export_meta_json` — meta.json `outputs` includes `stl` and/or `glb` paths
- `test_run_with_export_json_response` — output JSON `outputs` includes export paths
- `test_run_without_export_no_extra_outputs` — default behavior unchanged, only `step` and `script` in outputs

### Phase 3: Wire into run.py

Modify `app/src/cadtool/commands/run.py`:
1. Add `--export` option (optional string, default `None`)
2. After STEP export, if `--export` is set:
   - Parse comma-separated format names
   - For `stl`: call `exporters.export(shape, path, exportType="STL")`
   - For `glb`: call `export_glb(shape.val().wrapped, path)` from export module
   - Add paths to `outputs` dict in meta.json and output JSON

### Phase 4: Verify all existing tests unchanged

All 43 existing tests must still pass — no extra outputs when `--export` is not used.

---

## Verification

1. `pytest app/tests/ -v` — expect ~53 tests, all passing
2. Manual test:
   ```bash
   cd $(mktemp -d)
   cadtool init --name test
   echo 'import cadquery as cq
   result = cq.Workplane("XY").box(10,10,10)
   show_object(result)' > box.py
   cadtool run box.py --output box --export stl
   file v1_box/output.stl           # should say ASCII text (STL)
   cadtool run box.py --output mesh --export stl,glb --render iso
   file v2_mesh/output.glb          # should say data (binary GLB)
   ls v2_mesh/renders/              # iso.png
   ```
