# M5 — Error Handling & Failed Versions

## Goal

Record script execution failures on disk and in the manifest so agents have a complete history for debugging.

## Two Failure Categories

1. **CLI errors** (no manifest, script file missing) — error message only, no disk artifacts, exit 1.
2. **Script failures** (syntax errors, runtime errors, no `show_object()`) — recorded on disk with `_failed` suffix, tracked in manifest with `"status": "failed"`.

## Failed Directory Structure

```
v3_holes_failed/
  script.py       ← copy of the input script
  meta.json       ← error details
                  ← NO output.step
```

## Failed meta.json

```json
{
  "version": 3,
  "label": "holes",
  "status": "failed",
  "created": "2026-03-08T...",
  "error": "Script execution failed: name 'foo' is not defined",
  "script": "v3_holes_failed/script.py"
}
```

## Failed Manifest Entry

```json
{"version": 3, "label": "holes", "status": "failed", "path": "v3_holes_failed/"}
```

`current` does NOT advance on failure.

## Failed JSON Response

```json
{
  "command": "run",
  "status": "failed",
  "version": 3,
  "label": "holes",
  "error": "Script execution failed: ...",
  "path": "v3_holes_failed/"
}
```

## Version Numbering

Failed runs consume a version number.

## Files Modified

- `app/src/cadtool/commands/run.py` — failure recording logic
- `app/tests/test_run.py` — update 1 existing test, add ~6 new tests

## Acceptance

- ~31 tests, all passing
- CLI errors unchanged (no disk artifacts)
- Script failures produce `_failed` directories with meta.json + script.py
- Manifest tracks failed versions; `current` stays on last success
