"""agentcad skill — install or show the agent skill file."""

import json
from pathlib import Path

import click

from agentcad.runners import dispatch


SKILL_CONTENT = """\
---
name: agentcad
description: >
  CAD tool for AI agents. Use when the user asks you to design, model, or build
  a 3D object. agentcad executes build123d Python scripts and produces STEP
  files, PNG renders, mesh exports (STL/GLB/OBJ), and geometric metrics.
compatibility: Requires Python 3.10-3.12 and agentcad installed (pip install agentcad).
allowed-tools: Bash(agentcad:*)
---

# agentcad — CAD tool for AI agents

You have access to `agentcad`, a CLI that turns build123d Python scripts into 3D
geometry. All output is JSON. Every command returns `"command"` and `"status"` keys.

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
   - `viewer.html` — interactive 3D review viewer for the user (humans only;
     you can't render HTML). It opens automatically after a successful run.
     From v2, A=previous and B=current are already loaded with synchronized
     A/B, side-by-side, overlay, diff-image, and Parts-tab change review.

   Pass `--no-preview` only for tight parametric sweeps where latency matters.
   Pass `--no-view` only when browser launch would disrupt an unattended or
   high-volume run.

4. **Review with the user.** The generated viewer opens automatically. On v2+
   start with its previous/current comparison, then use A/B, Overlay, and Parts
   without selecting files manually. Use `agentcad view old.step new.step` only
   for an explicit non-adjacent comparison.

5. **Inspect if invalid.** If `is_valid: false` or geometry looks wrong:
   ```bash
   agentcad inspect v1_label/output.step
   ```

6. **Measure feature sizes.** For dimensions beyond top-level metrics:
   ```bash
   agentcad measure v1_label/output.step
   ```
   Use this for hole diameters, cylindrical boss diameters, edge lengths,
   face areas, and full per-feature measurements with `--features`.

7. **Check explicit feature requirements.** If the prompt names measurable
   holes, bores, or cylindrical bosses, write them into `spec.json` before
   final handoff:
   ```json
   {"features":[{"name":"bolt_holes","type":"cylinder","diameter_mm":6,"count":4}]}
   ```
   Then run:
   ```bash
   agentcad check-spec v1_label/output.step spec.json
   ```
   Revise the CAD if `passed` is false. `status: success` only means the
   comparison ran; `passed` is the actual spec-check result. If you include
   `axis`, copy it from `agentcad measure`'s `cylindrical_features[].axis`.

8. **Iterate.** Fix the script, run with a new `--output` label. Use
   `agentcad diff 1 2` to compare versions.

## Script writing rules

- `show_object(result)` is required — at least one call.
- These are pre-injected by default (no import needed):
  build123d primitives like `Box`, `Cylinder`, `Sphere`, `Plane`, plus
  `show_object`, `load_step`, `pick_face`, `pick_edge`, `fillet_edges`,
  `chamfer_edges`, `shell_faces`, `cut_pocket`, `boss`, `split_by_plane`,
  `replace_face`, `annular_boss`, and `raise_annulus`.
- For imported STEP annular edits, use the non-fuse workflow:
  ```python
  raw = load_step_shape("v1_vendor/output.step")
  result = raise_annulus(raw, center=(0, 0), inner_diameter=40,
                         outer_diameter=80, height=7, z=5)
  show_object(Compound(result))
  ```
- For OCP internals (`gp_Pnt`, `BRepPrimAPI`, etc.), import manually.
- CadQuery compatibility remains available for existing projects. See
  `agentcad docs runtimes` for that separate workflow.

## Key commands

| Command | Purpose |
|---------|---------|
| `agentcad init --name NAME` | Initialize project |
| `agentcad run SCRIPT --output LABEL` | Execute script, produce STEP + metrics |
| `agentcad run ... --dry-run` | Metrics only, no version consumed |
| `agentcad run ... --no-preview` | Suppress preview (on by default) |
| `agentcad run ... --no-view` | Suppress automatic browser review |
| `agentcad run ... --render iso,front` | PNG views |
| `agentcad run ... --export stl,glb` | Mesh export |
| `agentcad run ... --params k=v,k=v` | Override script parameters |
| `agentcad render STEP --view SPEC` | Post-hoc renders with camera control |
| `agentcad export STEP --format stl,glb` | Post-hoc mesh export |
| `agentcad measure STEP` | Dimensional report (overall metrics + feature sizes) |
| `agentcad check-spec STEP spec.json` | Pass/fail checklist against intended cylindrical features |
| `agentcad inspect STEP` | Topology report (validity, free edges) |
| `agentcad parts list REF` | List parts captured for a version |
| `agentcad parts show REF ID` | Show one versioned part by stable id |
| `agentcad diff REF1 REF2` | Compare versions |
| `agentcad context` | Project state |
| `agentcad docs [SECTION]` | Deep-dive docs (17 sections) |
| `agentcad view FILE [FILE_B]` | Open one model or an explicit synchronized A/B comparison |

## Debugging playbook

1. **Check metrics first** — `volume` and `dimensions` catch most issues.
2. **Read `preview.png`** — the 4-view composite. Fastest way to spot obvious problems.
3. **Read `diff.side_by_side`** if iterating — confirms your change did what you intended.
4. **Negative volume?** Wire winding is backwards (CW instead of CCW).
5. **Need a hole diameter or edge length?** Run `agentcad measure output.step`.
6. **Need to verify explicit hole/bore counts?** Write `spec.json`, then run
   `agentcad check-spec output.step spec.json`.
7. **is_valid: false?** Run `agentcad inspect` — check `free_edge_count` and shell status.
8. **Hollow shape?** `free_edge_count > 0` means open shell.
9. **Complex profiles (gears, splines)?** Use subtractive construction — cut from
   a blank cylinder/box instead of building up. See `agentcad docs patterns`.

## Patterns

- **Build at origin, then position:** Create geometry at origin, use `translate()`
  and `rotate()` to place it.
- **Compound vs fuse:** `Compound([...])` keeps assembly parts separate; use
  build123d's `+` operator to boolean-fuse solids.
- **Parametric scripts:** Top-level variable assignments become overridable via
  `--params`. Use this for iteration.
- **Named parts:** `show_object(shape, id="wheel_left", name="Left wheel",
  options={"color": "red"})` for stable part handles, per-part metrics, and
  colored GLB export.
"""


_CADQUERY_SCRIPT_RULES = """## Script writing rules

- This is a CadQuery compatibility project. Keep scripts on the CadQuery API.
- `show_object(result)` is required — at least one call.
- `cq`, `show_object`, and compatibility helpers are pre-injected, so a basic
  script needs no import:
  ```python
  part = cq.Workplane('XY').box(10, 20, 5)
  show_object(part)
  ```
- Helpers that operate on `TopoDS_Shape` use `.val().wrapped` as the bridge:
  ```python
  part = cq.Workplane('XY').box(10, 20, 5).val().wrapped
  moved = translate(part, 50, 0, 0)
  ```
- To show raw helper output:
  ```python
  show_object(cq.Workplane('XY').newObject([cq.Shape.cast(topo_shape)]))
  ```
- For OCP internals (`gp_Pnt`, `BRepPrimAPI`, etc.), import manually.

"""


_CADQUERY_PATTERNS = """## Patterns

- **Build at origin, then position:** Create geometry at origin, use
  `translate()` and `rotate()` to place it.
- **Compound vs union:** `makeCompound()` keeps assembly parts separate;
  `.union()` boolean-fuses solids.
- **Parametric scripts:** Top-level variable assignments become overridable via
  `--params`. Use this for iteration.
- **Named parts:** `show_object(shape, id="wheel_left", name="Left wheel",
  options={"color": "red"})` creates stable part handles, per-part metrics, and
  colored GLB output.
"""


def _cadquery_skill_content() -> str:
    """Create a CadQuery-only compatibility skill from the shared workflow."""
    content = SKILL_CONTENT
    content = content.replace(
        "a 3D object. agentcad executes build123d Python scripts and produces STEP\n"
        "  files, PNG renders, mesh exports (STL/GLB/OBJ), and geometric metrics.",
        "a 3D object in an existing CadQuery compatibility project. agentcad\n"
        "  produces STEP files, PNG renders, mesh exports (STL/GLB/OBJ), and metrics.",
    )
    content = content.replace(
        "You have access to `agentcad`, a CLI that turns build123d Python scripts into 3D\n"
        "geometry.",
        "You have access to `agentcad` in a CadQuery compatibility project. The CLI\n"
        "turns CadQuery Python scripts into 3D geometry.",
    )
    content = content.replace(
        "## First-time setup\n\n"
        "```bash\n"
        "agentcad init --name <project_name>\n"
        "agentcad --help   # Read this — it is your complete operational briefing\n"
        "```",
        "## CadQuery compatibility setup\n\n"
        "```bash\n"
        "agentcad init --name <project_name> --runtime cadquery\n"
        "agentcad --help   # Read the project-scoped compatibility briefing\n"
        "```",
    )
    content = content.replace(
        "1. **Write a script.** No imports needed — build123d primitives,\n"
        "   `show_object`, and agentcad edit helpers are pre-injected by default.\n"
        "   `show_object(result)` is required.",
        "1. **Write a script.** No imports needed — `cq`, `show_object`, and\n"
        "   CadQuery compatibility helpers are pre-injected. At least one\n"
        "   `show_object(result)` call is required.",
    )
    rules_start = content.index("## Script writing rules")
    commands_start = content.index("## Key commands")
    content = (
        content[:rules_start]
        + _CADQUERY_SCRIPT_RULES
        + content[commands_start:]
    )
    content = content.replace(
        "| `agentcad init --name NAME` | Initialize project |",
        "| `agentcad init --name NAME --runtime cadquery` | Initialize compatibility project |",
    )
    patterns_start = content.index("## Patterns")
    return content[:patterns_start] + _CADQUERY_PATTERNS


def _effective_runtime(runtime: str | None) -> str:
    return (
        runtime
        or dispatch.project_runtime(search_parents=True)
        or dispatch.DEFAULT_RUNTIME
    )


def _skill_content(runtime: str) -> str:
    if runtime == "cadquery":
        return _cadquery_skill_content()
    return SKILL_CONTENT


@click.group()
def skill():
    """Manage the agentcad agent skill."""


@skill.command()
@click.option(
    "--runtime",
    default=None,
    type=click.Choice(["cadquery", "build123d"]),
    help="Show the skill for an explicit runtime instead of the project mode.",
)
def show(runtime):
    """Print the skill file content as JSON."""
    effective_runtime = _effective_runtime(runtime)
    click.echo(json.dumps({
        "command": "skill show",
        "status": "success",
        "runtime": effective_runtime,
        "content": _skill_content(effective_runtime),
    }))


@skill.command()
@click.option(
    "--runtime",
    default=None,
    type=click.Choice(["cadquery", "build123d"]),
    help="Install the skill for an explicit runtime instead of the project mode.",
)
def install(runtime):
    """Install the agent skill to .claude/skills/agentcad/SKILL.md."""
    effective_runtime = _effective_runtime(runtime)
    skill_dir = Path.cwd() / ".claude" / "skills" / "agentcad"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(_skill_content(effective_runtime))

    click.echo(json.dumps({
        "command": "skill install",
        "status": "success",
        "runtime": effective_runtime,
        "path": str(skill_path),
        "message": "Skill installed. Claude Code will auto-discover it in this project.",
    }))
