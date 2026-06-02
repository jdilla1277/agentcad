"""agentcad skill — install or show the agent skill file."""

import json
from pathlib import Path

import click


SKILL_CONTENT = """\
---
name: agentcad
description: >
  CAD tool for AI agents. Use when the user asks you to design, model, or build
  a 3D object. agentcad executes build123d or CadQuery Python scripts and
  produces STEP files, PNG renders, mesh exports (STL/GLB/OBJ), and geometric metrics.
compatibility: Requires Python 3.10-3.12 and agentcad installed (pip install agentcad).
allowed-tools: Bash(agentcad:*)
---

# agentcad — CAD tool for AI agents

You have access to `agentcad`, a CLI that turns build123d or CadQuery Python scripts into
3D geometry. All output is JSON. Every command returns `"command"` and `"status"` keys.

## First-time setup

```bash
agentcad init --name <project_name>
agentcad --help   # Read this — it is your complete operational briefing
```

## Core workflow

1. **Write a script.** No imports needed — build123d primitives,
   `show_object`, and agentcad edit helpers are pre-injected by default.
   `show_object(result)` is required.

2. **Dry-run first** to check metrics without consuming a version:
   ```bash
   agentcad run script.py --output test --dry-run
   ```
   Check `volume`, `dimensions`, `is_valid` in the response.

3. **Run for real.** Visual feedback is on by default:
   ```bash
   agentcad run script.py --output label
   ```
   Every successful run produces (paths in the JSON response):
   - `preview.png` — 4-view composite (front, right, top, iso). **Read this**
     to confirm the part looks right before iterating. One image, all 4 angles.
   - `diff.side_by_side` — side-by-side PNG vs the most recent successful prior
     version. **Read this** when iterating to see what your change did.
   - `diff.overlay` — tinted (green prev, red this) overlay for subtle shifts.
     Read only if side-by-side didn't resolve the question.
   - `viewer.html` — interactive 3D viewer for the user (humans only; you can't
     render HTML). Mention it to the user so they open it.

   Pass `--no-preview` only for tight parametric sweeps where latency matters.

4. **Show the user.** After a successful build, open the interactive viewer:
   ```bash
   agentcad view v1_label/viewer.html   # or output.step / output.glb
   ```
   Users expect to see the result in a browser. Do this every run, unprompted.

5. **Inspect if invalid.** If `is_valid: false` or geometry looks wrong:
   ```bash
   agentcad inspect v1_label/output.step
   ```

6. **Iterate.** Fix the script, run with a new `--output` label. Use
   `agentcad diff 1 2` to compare versions.

## Script writing rules

- `show_object(result)` is required — at least one call.
- These are pre-injected by default (no import needed):
  build123d primitives like `Box`, `Cylinder`, `Sphere`, `Plane`, plus
  `show_object`, `load_step`, `pick_face`, `pick_edge`, `fillet_edges`,
  `chamfer_edges`, `shell_faces`, `cut_pocket`, `boss`, `split_by_plane`,
  and `replace_face`.
- CadQuery remains supported. Use `import cadquery as cq`, initialize with
  `agentcad init --runtime cadquery`, or pass `--runtime cadquery`.
- CadQuery helper paths operate on `TopoDS_Shape`. Bridge with `.val().wrapped`:
  ```python
  import cadquery as cq
  part = cq.Workplane('XY').box(10, 20, 5).val().wrapped
  moved = translate(part, 50, 0, 0)
  ```
- To show helper output:
  ```python
  import cadquery as cq
  show_object(cq.Workplane('XY').newObject([cq.Shape.cast(topo_shape)]))
  ```
- For OCP internals (`gp_Pnt`, `BRepPrimAPI`, etc.), import manually.

## Key commands

| Command | Purpose |
|---------|---------|
| `agentcad init --name NAME` | Initialize project |
| `agentcad run SCRIPT --output LABEL` | Execute script, produce STEP + metrics |
| `agentcad run ... --dry-run` | Metrics only, no version consumed |
| `agentcad run ... --no-preview` | Suppress preview (on by default) |
| `agentcad run ... --render iso,front` | PNG views |
| `agentcad run ... --export stl,glb` | Mesh export |
| `agentcad run ... --params k=v,k=v` | Override script parameters |
| `agentcad render STEP --view SPEC` | Post-hoc renders with camera control |
| `agentcad export STEP --format stl,glb` | Post-hoc mesh export |
| `agentcad inspect STEP` | Topology report (validity, free edges) |
| `agentcad diff REF1 REF2` | Compare versions |
| `agentcad context` | Project state |
| `agentcad docs [SECTION]` | Deep-dive docs (16 sections) |
| `agentcad view FILE` | **Run this after every successful build** — opens GLB/STEP in the user's browser |

## Debugging playbook

1. **Check metrics first** — `volume` and `dimensions` catch most issues.
2. **Read `preview.png`** — the 4-view composite. Fastest way to spot obvious problems.
3. **Read `diff.side_by_side`** if iterating — confirms your change did what you intended.
4. **Negative volume?** Wire winding is backwards (CW instead of CCW).
5. **is_valid: false?** Run `agentcad inspect` — check `free_edge_count` and shell status.
6. **Hollow shape?** `free_edge_count > 0` means open shell.
7. **Complex profiles (gears, splines)?** Use subtractive construction — cut from
   a blank cylinder/box instead of building up. See `agentcad docs patterns`.

## Patterns

- **Build at origin, then position:** Create geometry at origin, use `translate()`
  and `rotate()` to place it.
- **Compound vs Union:** `makeCompound()` for assemblies (parts stay separate),
  `.union()` for boolean fuse into one solid.
- **Parametric scripts:** Top-level variable assignments become overridable via
  `--params`. Use this for iteration.
- **Named parts:** `show_object(shape, name="wheel", options={"color": "red"})`
  for per-part metrics and colored GLB export.
"""


@click.group()
def skill():
    """Manage the agentcad agent skill."""


@skill.command()
def show():
    """Print the skill file content as JSON."""
    click.echo(json.dumps({
        "command": "skill show",
        "status": "success",
        "content": SKILL_CONTENT,
    }))


@skill.command()
def install():
    """Install the agent skill to .claude/skills/agentcad/SKILL.md."""
    skill_dir = Path.cwd() / ".claude" / "skills" / "agentcad"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(SKILL_CONTENT)

    click.echo(json.dumps({
        "command": "skill install",
        "status": "success",
        "path": str(skill_path),
        "message": "Skill installed. Claude Code will auto-discover it in this project.",
    }))
