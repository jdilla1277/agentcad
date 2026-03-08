# Milestone 4: `cadtool run` — CadQuery Script Execution & STEP Export

## Context

M1-M3 built the project scaffolding and 2D geometry primitives. M4 is the pivot to real 3D CAD: execute a CadQuery Python script and produce a versioned STEP file. After M4, an agent can write a 3D CAD script and cadtool turns it into real geometry.

**Baseline:** 58 tests, 6 commands (`init`, `add-rect`, `add-circle`, `list`, `delete`, `get`).

## Scope (M4 only)

- `cadtool run <script> --output <label>` — execute script, export STEP
- Version directories with auto-incrementing numbers
- `meta.json` per version
- Manifest updated with `versions` array
- **Out of scope:** `--input` chaining, `--formats`, error recovery for bad scripts (M5), PNG rendering (M6)

## Command

```bash
cadtool run script.py --output v1
cadtool run script.py --output v2_mounting_holes
```

## Success Response

```json
{
  "command": "run",
  "status": "success",
  "version": 1,
  "label": "v1",
  "outputs": {
    "step": "v1/output.step",
    "script": "v1/script.py"
  }
}
```

## Error Responses

No manifest:
```json
{"command": "run", "status": "error", "message": "cadtool.json not found. Run 'cadtool init' first."}
```

Script not found:
```json
{"command": "run", "status": "error", "message": "Script file 'foo.py' not found"}
```

CadQuery execution failure:
```json
{"command": "run", "status": "error", "message": "Script execution failed: <error details>"}
```

## Version Directory Structure

```
project/
  cadtool.json
  v1/
    script.py          ← copy of the input script
    output.step        ← STEP export
    meta.json          ← version metadata
  v2_mounting_holes/
    script.py
    output.step
    meta.json
```

## Manifest Changes

`cadtool init` adds `"versions": []` to the manifest. `cadtool run` appends to it:

```json
{
  "name": "project",
  "version": "0.1.0",
  "created": "...",
  "objects": [],
  "versions": [
    {"version": 1, "label": "v1", "status": "success", "path": "v1/"}
  ],
  "current": "v1"
}
```

The `objects` array stays for now (primitive commands still work). M9 cleanup removes it.

## meta.json Schema

```json
{
  "version": 1,
  "label": "v1",
  "status": "success",
  "created": "2026-03-07T...",
  "script": "v1/script.py",
  "outputs": {
    "step": "v1/output.step"
  }
}
```

## Script Execution via CQGI

```python
from cadquery import cqgi

script_source = Path(script_path).read_text()
build_result = cqgi.parse(script_source).build()

if build_result.success:
    shape = build_result.first_result.shape
    cq.exporters.export(shape, str(step_path))
else:
    # error handling
```

Scripts use `show_object(result)` to declare output (standard CadQuery convention). CQGI intercepts this.

## Dependencies

Added `cadquery>=2.0` to `pyproject.toml`. Requires Python 3.12 (OCP bindings not available for 3.14). Venv recreated with `/opt/homebrew/bin/python3.12`.

## File Tree

### New files (3)
```
app/src/cadtool/commands/run.py     ← cadtool run logic
app/tests/test_run.py              ← ~10 tests
prd/m4_plan.md                     ← this plan
```

### Modified files (4)
```
app/src/cadtool/cli.py             ← register run command
app/src/cadtool/commands/init.py   ← add "versions": [] to manifest
app/pyproject.toml                 ← add cadquery dependency
app/tests/test_cli.py             ← +1 test (run registration)
app/tests/test_init.py            ← +1 test (versions key)
```

## TDD Phases

### Phase 0: Dependencies & init update
1. Add `cadquery>=2.0` to pyproject.toml, recreate venv with Python 3.12
2. Verify `python -c "import cadquery"` works

### Phase 1: Init adds `versions` array — RED then GREEN
3. Add `test_init_manifest_has_empty_versions_array` to `test_init.py` — RED
4. Update `init.py`: add `"versions": []` to manifest dict — GREEN
5. **Checkpoint: 59 tests**

### Phase 2: `cadtool run` — core tests — RED then GREEN
6. Create `test_run.py` with tests:
   - `test_run_no_manifest_error` — exit 1, error JSON with `"command": "run"`
   - `test_run_script_not_found_error` — exit 1, error JSON
   - `test_run_creates_version_directory` — `v1/` exists after run
   - `test_run_copies_script_to_version_dir` — `v1/script.py` exists and matches input
   - `test_run_produces_step_file` — `v1/output.step` exists with non-zero size
   - `test_run_success_json` — correct response schema
   - `test_run_creates_meta_json` — `v1/meta.json` exists with correct schema
   - `test_run_updates_manifest_versions` — manifest `versions` array has entry
   - `test_run_auto_increments_version` — second run gets version 2
   - `test_run_label_in_directory_name` — `--output my_label` creates `v1_my_label/`
7. Create `commands/run.py` with full implementation
8. Register in `cli.py`
9. **Checkpoint: 69 tests**

### Phase 3: CLI registration test
10. Add `test_run_subcommand_registered` to `test_cli.py` — GREEN
11. **Checkpoint: 70 tests**

## Test Fixtures

Tests that actually execute CadQuery need a valid script file:

```python
SIMPLE_BOX_SCRIPT = """\
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10)
show_object(result)
"""
```

Tests that don't need CadQuery execution (no-manifest, script-not-found) don't need the fixture.

## Verification

1. `pytest app/tests/ -v` — all ~70 tests pass
2. `cadtool --help` — shows `run` command
3. Manual integration:
   ```bash
   cd $(mktemp -d)
   cadtool init --name demo
   cat > box.py << 'EOF'
   import cadquery as cq
   result = cq.Workplane("XY").box(50, 30, 20)
   show_object(result)
   EOF
   cadtool run box.py --output first_box
   ls v1_first_box/           # script.py, output.step, meta.json
   cat v1_first_box/meta.json
   cadtool run box.py --output second
   ls v2_second/              # version auto-incremented
   ```
4. Error cases:
   ```bash
   cd $(mktemp -d)
   cadtool run box.py --output foo   # fail: no manifest
   cadtool init
   cadtool run missing.py --output foo  # fail: script not found
   ```
