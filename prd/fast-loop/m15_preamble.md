# M15 — Script Preamble

**Epic:** Fast Loop
**Status:** Planned
**Goal:** Scripts run with `cq`, `show_object`, and all helpers pre-injected. Agent writes pure design intent.

## Modified Files

- `app/src/cadtool/commands/run.py` — prepend preamble to script source before CQGI
- `app/src/cadtool/commands/docs.py` — add "preamble" section

## Design

Single-line preamble prepended to script source:

```python
PREAMBLE = "import cadquery as cq; from cadtool.helpers import loft_sections, tapered_sweep, naca_wire, mirror_fuse\n"
```

- Explicit imports in user scripts still work (Python re-import is no-op)
- Error line numbers offset by 1 line (acceptable)

## Tests (~5)

- Script without any imports still runs
- Script using helpers without importing them works
- Script with explicit `import cadquery as cq` still works
- Error line numbers are reasonable
- `cadtool docs preamble` lists available names
