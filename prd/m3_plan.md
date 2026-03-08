# Milestone 3: Geometry Enrichment (`--label`, `count`, `delete`, `get`)

## Context

M2 shipped the core geometry commands. Agent friction testing surfaced two quick wins (shape labels, count in list) and revealed that agents have no way to delete or inspect individual objects — they can only append and list-all. This milestone closes those gaps before the heavier `cadtool run` work. No new dependencies.

**Baseline:** 38 tests, 4 commands (`init`, `add-rect`, `add-circle`, `list`).

## File Tree

### New files (4)
```
app/src/cadtool/commands/delete.py    ← cadtool delete --id N
app/src/cadtool/commands/get.py       ← cadtool get --id N
app/tests/test_delete.py              ← 7 tests
app/tests/test_get.py                 ← 6 tests
```

### Modified files (6)
```
app/src/cadtool/commands/add_rect.py    ← add optional --label
app/src/cadtool/commands/add_circle.py  ← add optional --label
app/src/cadtool/commands/list_objects.py ← add "count" to response
app/src/cadtool/cli.py                  ← register delete, get
app/tests/test_add_rect.py              ← +2 tests (label)
app/tests/test_add_circle.py            ← +2 tests (label)
app/tests/test_list.py                  ← +1 test (count)
app/tests/test_cli.py                   ← +2 tests (registration)
```

## Design Decisions

- **Label is optional, absent when not given.** The `"label"` key only appears in the object dict when `--label` is passed. No `null` values — agents check `"label" in obj`.
- **`count` is always present in `list`.** Even when zero. Placed before `"objects"` in the response.
- **`delete` removes the object, does not reassign IDs.** Consistent with `max(ids) + 1` strategy validated in M2.
- **`delete` and `get` use `--id` (int).** Click param name is `obj_id` to avoid shadowing Python's `id` builtin.
- **ID not found → error JSON with exit code 1.** Same contract as every other command.

## Updated Object Schemas

- **Rect without label:** `{"id": 1, "type": "rect", "x": 0.0, "y": 0.0, "width": 100.0, "height": 50.0}`
- **Rect with label:** `{"id": 1, "type": "rect", "x": 0.0, "y": 0.0, "width": 100.0, "height": 50.0, "label": "body"}`
- **Circle with label:** `{"id": 2, "type": "circle", "cx": 0.0, "cy": 0.0, "radius": 25.0, "label": "head"}`

## TDD Implementation Order

### Phase 1: Shape labels on `add-rect` — RED then GREEN
1. Add 2 tests to `test_add_rect.py`:
   - `test_add_rect_label_stored_in_object` — `--label body` → object has `"label": "body"`
   - `test_add_rect_no_label_omits_key` — no `--label` → `"label" not in obj`
2. Add `--label` option to `add_rect.py`, conditionally include in object dict
3. **Checkpoint: 40 tests**

### Phase 2: Shape labels on `add-circle` — RED then GREEN
4. Add 2 tests to `test_add_circle.py`:
   - `test_add_circle_label_stored_in_object`
   - `test_add_circle_no_label_omits_key`
5. Add `--label` option to `add_circle.py`
6. **Checkpoint: 42 tests**

### Phase 3: `count` in `list` response — RED then GREEN
7. Add 1 test to `test_list.py`:
   - `test_list_response_includes_count`
8. Add `"count": len(objects)` to list response
9. **Checkpoint: 43 tests**

### Phase 4: `cadtool delete --id N` — RED then GREEN
10. Write `test_delete.py` with 7 tests:
    - `test_delete_no_manifest_error`
    - `test_delete_removes_object_from_manifest`
    - `test_delete_success_json`
    - `test_delete_nonexistent_id_error`
    - `test_delete_preserves_other_objects`
    - `test_delete_does_not_reassign_ids`
    - `test_delete_echoes_deleted_object`
11. Implement `commands/delete.py`, register in `cli.py`
12. **Checkpoint: 50 tests**

### Phase 5: `cadtool get --id N` — RED then GREEN
13. Write `test_get.py` with 6 tests:
    - `test_get_no_manifest_error`
    - `test_get_returns_object_by_id`
    - `test_get_success_json`
    - `test_get_nonexistent_id_error`
    - `test_get_includes_label_when_present`
    - `test_get_omits_label_when_absent`
14. Implement `commands/get.py`, register in `cli.py`
15. **Checkpoint: 56 tests**

### Phase 6: CLI registration tests
16. Add 2 tests to `test_cli.py`:
    - `test_delete_subcommand_registered`
    - `test_get_subcommand_registered`
17. **Checkpoint: 58 tests**

## Verification

1. `pytest app/tests/ -v` — all 58 tests pass
2. `cadtool --help` — shows `init`, `add-rect`, `add-circle`, `list`, `delete`, `get`
3. Manual integration:
   ```bash
   cd $(mktemp -d)
   cadtool init --name demo
   cadtool add-rect --x 0 --y 0 --width 100 --height 50 --label body
   cadtool add-circle --cx 50 --cy 25 --radius 15 --label head
   cadtool list
   cadtool get --id 1
   cadtool delete --id 2
   cadtool list
   ```
4. Error cases:
   ```bash
   cd $(mktemp -d)
   cadtool delete --id 1        # fail: no init
   cadtool init
   cadtool get --id 999         # fail: not found
   cadtool delete --id 999      # fail: not found
   ```
