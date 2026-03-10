# Friction Log: Building a Helical Gear with cadtool

## Task
Generate a helical gear (32 teeth, module 16, 25° helix, 20° pressure angle, 170mm bore, 100mm height) and export as GLB.

## What went well
- **Installation** was straightforward - single pip install, `cadtool init` just works
- **CLI design** is clean - `run`, `render`, `export`, `view` are intuitive commands
- **JSON output** from every command is great for programmatic use - metrics, file paths, status all structured
- **Pre-injected `cq` and `show_object`** means scripts stay clean without boilerplate imports
- **`--export glb`** on `run` is convenient - no separate export step needed
- **Metrics** (volume, bounding box, validity) are invaluable for debugging geometry without opening a viewer
- **Docs** via `cadtool docs` are comprehensive and well-organized

## Friction points

### 1. Python version mismatch (high friction)
The venv had Python 3.14, but OCP requires 3.10-3.12. The install "succeeded" (pip installed cadquery without OCP bindings), then `cadtool run` failed with cryptic `import_error` validation errors for every OCP module. Had to recreate the venv with the right Python.
- **Suggestion**: Check Python version during `pip install` or `cadtool init` and warn/fail early.

### 2. Validation errors don't distinguish "not installed" from "wrong Python" (medium friction)
The validation output listed every OCP module as "not found" but didn't hint that this was a Python version compatibility issue.
- **Suggestion**: If cadquery imports fail, check Python version and suggest the fix.

### 3. No geometry debugging tools (high friction)
When the gear came out hollow, I had no way to inspect *why* through cadtool. I had to reason about winding order, wire self-intersection, and face creation purely from volume numbers and dark renders. A command like `cadtool inspect output.step --faces --shells` showing topology info (number of shells, open/closed, face normals) would have saved significant debugging time.
- **Suggestion**: Add a `cadtool inspect` command that reports shell topology, open edges, face orientation, etc.

### 4. `is_valid: false` with no explanation (high friction)
Multiple versions reported `is_valid: false` but the output gave no hint about *what* was invalid. Was it an open shell? Self-intersection? Inverted normals?
- **Suggestion**: When `is_valid` is false, include a brief reason (e.g., "open shell", "self-intersecting", "negative volume detected").

### 5. Negative volume not flagged as a warning (medium friction)
Version 1 reported `volume: -18405241.8796` without any warning. Negative volume indicates inverted normals, which is almost always a bug. I initially missed this signal.
- **Suggestion**: If volume is negative, add a warning in the output: `"warnings": ["Negative volume detected - shape may have inverted normals"]`.

### 6. `cadtool render` is very dark (medium friction)
All renders came out nearly black, making it very hard to tell solid from hollow geometry. The dark default lighting/material made visual debugging unreliable - I couldn't tell if I was looking at a solid face or seeing through a hole.
- **Suggestion**: Use a lighter default material or add ambient lighting so surfaces are clearly distinguishable from the background.

### 7. `cadtool view` failed on relative paths (low friction)
`cadtool view v5_helical_gear_v5/output.glb` crashed with `ValueError: relative path can't be expressed as a file URI`. Required absolute path.
- **Suggestion**: Resolve relative paths to absolute before converting to file URI.

### 8. Version directory collision (low friction)
Re-running with `--output spur_test` after a failed run crashed with `FileExistsError` because the version directory already existed from a previous attempt.
- **Suggestion**: Either overwrite or auto-increment the version label.

### 9. No `--render` support for custom angles in `run` (low friction)
`cadtool run ... --render 30,50` failed with a KeyError. Custom angle renders only work via `cadtool render`. Would be nice to support them in `run` too.

## Summary
cadtool's core workflow (write script → run → render → export) is solid. The biggest gaps are in **debugging failed geometry** - when something goes wrong, you're flying blind. Better validation messages, shape inspection tools, and brighter renders would dramatically reduce iteration time for complex models.

Total time: ~20 minutes of iteration, most spent diagnosing the hollow gear bug. With better geometry debugging tools it would have been ~5 minutes.
