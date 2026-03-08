# Milestone 2: Geometry Commands (`add-rect`, `add-circle`, `list`)

## Context

M1 established the package skeleton and `cadtool init`. This milestone makes cadtool actually useful by letting agents create and inspect geometry. After M2, an agent can initialize a project, add shapes, and query what's in the drawing — all via structured JSON.

## File Tree

### New files (7)
```
app/src/cadtool/manifest.py            ← shared load/save + MANIFEST_FILE constant
app/src/cadtool/commands/add_rect.py   ← cadtool add-rect
app/src/cadtool/commands/add_circle.py ← cadtool add-circle
app/src/cadtool/commands/list_objects.py ← cadtool list
app/tests/test_add_rect.py            ← 8 tests
app/tests/test_add_circle.py          ← 8 tests
app/tests/test_list.py                ← 6 tests
```

### Modified files (3)
```
app/src/cadtool/cli.py                ← register 3 new commands
app/src/cadtool/commands/init.py       ← add "objects": [] to manifest, import MANIFEST_FILE from manifest.py
app/tests/test_init.py                ← 1 new test for objects key
```

## Design Decisions

- **Storage**: Shapes stored in `"objects"` array inside `cadtool.json` — single file, single source of truth
- **IDs**: Auto-incrementing integers starting at 1, via `max(existing ids) + 1` (survives future deletions)
- **Shared I/O**: New `manifest.py` with `load_manifest()` / `save_manifest()` + `MANIFEST_FILE` constant (moved from init.py, re-exported for backward compat)
- **Validation**: width/height/radius must be > 0; no manifest = error exit code 1

## Shape Schemas

**Rectangle**: `{"id": 1, "type": "rect", "x": 0.0, "y": 0.0, "width": 100.0, "height": 50.0}`
**Circle**: `{"id": 2, "type": "circle", "cx": 0.0, "cy": 0.0, "radius": 25.0}`

## TDD Implementation Order

### Phase 0: Extract shared manifest module (refactor only)
1. Create `manifest.py` with `MANIFEST_FILE`, `load_manifest()`, `save_manifest()`
2. Update `init.py` to import from `manifest.py` (re-export `MANIFEST_FILE`)
3. Run tests — all 12 pass (refactor checkpoint)

### Phase 1: Update init to include objects — RED then GREEN
4. Add `test_init_manifest_has_empty_objects_array` to `test_init.py` — RED
5. Add `"objects": []` to manifest dict in `init.py` — GREEN (13 tests)

### Phase 2: `add-rect` — RED then GREEN
6. Write `test_add_rect.py` with 8 tests — RED
   - no manifest error, creates object in manifest, success JSON, correct dimensions, id=1 first, auto-increment id, negative width error, zero height error
7. Implement `commands/add_rect.py`, register in `cli.py` — GREEN (21 tests)

### Phase 3: `add-circle` — RED then GREEN
8. Write `test_add_circle.py` with 8 tests — RED
   - no manifest error, creates object, success JSON, correct values, id=1 first, cross-type id continuity, negative radius error, zero radius error
9. Implement `commands/add_circle.py`, register in `cli.py` — GREEN (29 tests)

### Phase 4: `list` — RED then GREEN
10. Write `test_list.py` with 6 tests — RED
    - no manifest error, empty project returns [], returns all objects, correct types, correct ids, valid JSON
11. Implement `commands/list_objects.py`, register in `cli.py` — GREEN (35 tests)

### Phase 5: CLI registration tests
12. Add 3 tests to `test_cli.py` (add-rect/add-circle/list registered) — GREEN (38 tests)

## Post-implementation fixes (from friction log)

### Fixed: "No manifest" error missing `command` key
`load_manifest()` now accepts an optional `command` parameter. All callers pass their command name so error JSON always includes `"command"`.

### Fixed: `add-*` responses now echo the full object
Success responses include an `"object"` key with the complete shape dict, saving agents a round trip to `list`.

## Verification

1. `pytest app/tests/ -v` — all 38 tests pass
2. `cadtool --help` — shows `init`, `add-rect`, `add-circle`, `list`
3. Manual integration:
   ```bash
   cd $(mktemp -d)
   cadtool init --name demo
   cadtool add-rect --x 0 --y 0 --width 100 --height 50
   cadtool add-circle --cx 50 --cy 25 --radius 15
   cadtool list
   cat cadtool.json
   ```
4. Error cases:
   ```bash
   cd $(mktemp -d)
   cadtool add-rect --x 0 --y 0 --width 10 --height 10   # fail: no init
   cadtool init
   cadtool add-rect --x 0 --y 0 --width -5 --height 10   # fail: negative width
   cadtool add-circle --cx 0 --cy 0 --radius 0            # fail: zero radius
   ```
