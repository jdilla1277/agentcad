# Friction Log — Round 2: Labels, Delete, Get (2026-03-07)

**Tester:** Claude Code (AI agent)
**Scope:** 5 journeys testing `--label`, `delete`, `get`, ID stability, and error cases
**Overall verdict:** Nearly frictionless. Every feature from the previous friction log has been addressed. Only cosmetic issues remain.

---

## Journey 1: Labeled Drawing (Happy Path)

```
cadtool init --name smiley                                          → success
cadtool add-circle --cx 150 --cy 150 --radius 120 --label "head"   → id 1, echoes full object
cadtool add-circle --cx 110 --cy 110 --radius 15 --label "left eye"  → id 2
cadtool add-circle --cx 190 --cy 110 --radius 15 --label "right eye" → id 3
cadtool add-rect --x 143 --y 135 --width 14 --height 25 --label "nose"  → id 4
cadtool add-rect --x 110 --y 190 --width 80 --height 12 --label "mouth" → id 5
cadtool list   → count=5, all objects have labels
cadtool get --id 1  → full circle with label "head"
```

**Result: Clean pass. Zero friction.**

Three things that were fixed since the last friction log:
- `add-*` responses now echo back the full object — no extra `list` call needed.
- `list` includes a `count` field.
- `--label` works exactly as proposed.

The `get` command is a great addition. Being able to fetch a single object by ID without parsing the full list is exactly what an agent needs.

---

## Journey 2: Delete + ID Stability

```
cadtool delete --id 3          → success
cadtool list                   → count=4, ids [1,2,4,5]
cadtool add-circle ... --label "right eye v2"  → id 6
cadtool get --id 6             → correct object
```

**Result: Clean pass.**

- Delete removes the right object and nothing else.
- IDs are never reassigned — id 3 is gone, new shape gets id 6 (max was 5 + 1).
- `count` updates correctly from 5 → 4 → 5.

### Minor observation: delete response doesn't echo the deleted object

`delete` returns `{"command": "delete", "status": "success", "id": 3}` but not what was deleted. Compare with `add-*` which now echoes the full object. For symmetry and agent convenience, including the deleted object in the response would let an agent confirm it deleted the right thing without a preceding `get`:

```json
{"command": "delete", "status": "success", "id": 3, "deleted": {"id": 3, "type": "circle", ...}}
```

**Severity:** Very low. The agent already knows what it asked to delete. Nice-to-have for audit trails.

---

## Journey 3: No Label (Key Absence)

```
cadtool add-rect --x 0 --y 0 --width 10 --height 10  → id 7
cadtool get --id 7  → {"id": 7, "type": "rect", "x": 0.0, "y": 0.0, "width": 10.0, "height": 10.0}
```

**Result: Correct.** The `label` key is absent from the object, not present with a null value. This is the right design — an agent can check `if "label" in obj` rather than `if obj.get("label") is not None`. Cleaner parsing, smaller payloads.

---

## Journey 4: Delete Down to Empty

```
cadtool init
cadtool add-rect --x 0 --y 0 --width 10 --height 10  → id 1
cadtool delete --id 1  → success
cadtool list  → count=0, objects=[]
```

**Result: Clean pass.** Empty state is handled gracefully. `count: 0` and `objects: []` — no nulls, no missing keys, no special-case output. An agent checking `response["count"] == 0` works without edge-case handling.

---

## Journey 5: Error Cases

### No manifest errors

```
cadtool delete --id 1  → {"status": "error", "message": "cadtool.json not found...", "command": "delete"}  EXIT: 1
cadtool get --id 1     → {"status": "error", "message": "cadtool.json not found...", "command": "get"}     EXIT: 1
```

**Fixed since last friction log.** The `command` key is now present in "no manifest" errors. Previously this was missing and flagged as a medium-severity issue. Resolved.

### ID not found errors

```
cadtool delete --id 99  → {"command": "delete", "status": "error", "message": "Object with id 99 not found"}  EXIT: 1
cadtool get --id 99     → {"command": "get", "status": "error", "message": "Object with id 99 not found"}    EXIT: 1
```

**Clean.** Correct exit code, valid JSON, clear message, `command` key present.

### Minor inconsistency: `command` key position

In "no manifest" errors, the key order is `status, message, command`. In all other responses, `command` comes first. This doesn't affect parsing (JSON is unordered) but suggests the no-manifest error path constructs its dict differently from the command-specific paths.

**Severity:** Cosmetic. No functional impact.

---

## Previous Issues — Status Check

| Issue from Round 1 | Status |
|---|---|
| "No manifest" error missing `command` key | **Fixed** |
| `add-*` responses don't echo shape params | **Fixed** — full object now included |
| No shape labels | **Fixed** — `--label` flag works |
| No `count` in `list` | **Fixed** |

All four actionable items from the first friction log have been addressed.

---

## Remaining Friction (Prioritized)

| # | Issue | Severity | Notes |
|---|-------|----------|-------|
| 1 | No visual output (SVG/render) | Medium | Still the biggest gap for agent spatial reasoning. Carried forward from round 1. |
| 2 | `delete` doesn't echo the deleted object | Very low | Symmetry with `add-*` echo. Nice-to-have. |
| 3 | `command` key position inconsistent in no-manifest errors | Cosmetic | JSON is unordered — no functional impact. |

---

## Overall Assessment

This is a tight, well-designed agent tool at this stage. The JSON contract is consistent, error handling is correct, IDs are stable, labels work cleanly with proper key-absence semantics, and every previous friction point has been addressed. The only meaningful gap left is visual output — everything else is polish.
