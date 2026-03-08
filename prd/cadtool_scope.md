# cadtool
## V0 Scope Document
*A CLI tool for AI-agent-driven parametric CAD design*

---

## Overview

cadtool is a command-line tool that enables AI coding agents (like Claude Code) to create, iterate on, and inspect 3D CAD models programmatically. The agent writes CadQuery scripts; cadtool handles geometry, export, rendering, and a structured version history. Humans review and approve the output before any downstream handoff.

The core loop is:

1. Human describes a design problem to Claude Code in natural language
2. Claude Code writes a CadQuery script and runs cadtool
3. cadtool produces a STEP file, 3D renders, and a structured JSON log
4. Claude Code inspects renders, iterates, and requests custom views as needed
5. Human reviews the final version and signs off

---

## Design Principles

- **Stateless file-in / file-out.** No session state. Each invocation takes an optional input STEP and produces a new versioned output. The file on disk is the state.
- **Every operation returns structured JSON.** Consistent schema regardless of command. The agent never has to parse prose to understand what happened.
- **Failed versions are kept.** A failed run writes a `_failed` suffixed file and returns an error in the JSON. The agent can inspect failures as part of its reasoning trail.
- **Human review is first-class.** GLB files open natively in macOS Preview with 3D spin. PNG renders give the agent visual inspection capability. Both serve the human sign-off step.
- **Agent discoverability over convention.** The tool does not assume a CLAUDE.md or any host project structure. It ships with `--help`, `cadtool docs`, and `cadtool context` for provider-agnostic discoverability.

---

## V0 Command Set

### `cadtool init`

Initializes a new cadtool project in the current directory. Creates the project folder structure and top-level manifest.

```bash
cadtool init
cadtool init --name enclosure
```

---

### `cadtool run`

The primary command. Executes a CadQuery Python script and produces versioned outputs.

```bash
cadtool run script.py --output v1
cadtool run script.py --output v1 --formats step,glb,obj,png
cadtool run script.py --input v1/output.step --output v2_mounting_holes --formats step,png-iso,png-top
```

Available formats:

| Flag | Description |
|------|-------------|
| `step` | STEP file — primary geometry output, vendor-neutral |
| `glb` | GLB 3D file — opens in macOS Preview, for human review |
| `obj` | Wavefront OBJ — alternate mesh format |
| `png` | All four standard render angles (front, side, top, iso) |
| `png-front` | Front view render |
| `png-side` | Side view render |
| `png-top` | Top-down render |
| `png-iso` | Isometric render |

Default formats if `--formats` is omitted: `step`, `png`

---

### `cadtool render`

Renders additional views of an existing version without bumping the version number. Designed for agent inspection — the agent can request specific views to verify geometry before deciding on next steps.

```bash
cadtool render v1/output.step --view iso
cadtool render v1/output.step --view top --zoom 2
cadtool render v1/output.step --view "45,30" --focus "10,10,5" --name hole_check
```

Named renders are saved into the version's `renders/` folder and recorded in `meta.json`. The human reviewer sees exactly what the agent was checking.

---

### `cadtool context`

Returns a concise summary of the tool's capabilities and current project state. Designed to be called by any coding agent at the start of a session to orient itself — provider-agnostic, no CLAUDE.md required.

```bash
cadtool context
cadtool context --json
```

---

### `cadtool docs`

Returns full markdown documentation. Agents can request specific sections.

```bash
cadtool docs
cadtool docs render
cadtool docs schema
```

---

## Output Schema

Every cadtool invocation returns JSON to stdout. The schema is consistent regardless of command.

### Successful run

```json
{
  "version": 2,
  "label": "v2_mounting_holes",
  "status": "success",
  "instruction": "cadtool run script.py --input v1/output.step --output v2_mounting_holes",
  "description": "Added 4x M3 through-holes at corner positions 5mm from each edge.",
  "input": "v1/output.step",
  "script": "v2_mounting_holes/script.py",
  "outputs": {
    "step": "v2_mounting_holes/output.step",
    "glb": "v2_mounting_holes/output.glb",
    "renders": {
      "iso": "v2_mounting_holes/renders/iso.png",
      "top": "v2_mounting_holes/renders/top.png"
    }
  },
  "warnings": []
}
```

### Failed run

```json
{
  "version": 3,
  "label": "v3_failed",
  "status": "failed",
  "error": "self-intersecting geometry at face 12",
  "outputs": {
    "step": "v3_failed/output_failed.step"
  },
  "warnings": []
}
```

No render is produced on failure. The failed STEP file is preserved for agent inspection. The `_failed` suffix signals to human reviewers that the version can be ignored.

---

## File Structure

```
project/
  cadtool.json                  ← top-level manifest
  v1/
    script.py
    output.step
    output.glb
    output.obj
    renders/
      iso.png
      front.png
      hole_check.png            ← agent-requested custom view
    meta.json
  v2_mounting_holes/
    script.py
    output.step
    renders/
      iso.png
      top.png
    meta.json
  v3_failed/
    script.py
    output_failed.step
    meta.json
```

### cadtool.json (top-level manifest)

```json
{
  "project": "enclosure",
  "created": "2024-01-15",
  "current": "v2_mounting_holes",
  "versions": [
    {
      "version": 1,
      "label": null,
      "status": "success",
      "description": "Basic enclosure 50x30x20mm",
      "path": "v1/"
    },
    {
      "version": 2,
      "label": "mounting_holes",
      "status": "success",
      "description": "Added 4x M3 mounting holes at corners",
      "path": "v2_mounting_holes/"
    }
  ]
}
```

The agent reads `cadtool.json` first to orient itself, then dives into individual `meta.json` files only if it needs detail.

---

## Technical Stack

| Component | Choice |
|-----------|--------|
| Geometry kernel | Open CASCADE via CadQuery (Python) |
| Scripting | CadQuery Python scripts written by the agent |
| STEP export | Open CASCADE native — exact geometry, no tessellation |
| GLB export | Tessellated via pythonOCC, for human review in macOS Preview |
| OBJ export | Tessellated intermediate format |
| Headless rendering | pythonOCC offscreen rendering (Mac v0), EGL/OSMesa (Linux v1+) |
| Render output | PNG, multiple angles, custom views on demand |
| Distribution (v0) | `pip install git+https://github.com/...` |

Linux support is explicitly out of scope for v0. The rendering layer behaves differently on Linux (EGL/OSMesa headless OpenGL) and will be addressed in v1 or v2 once the Mac experience is validated.

---

## Out of Scope for V0

- Linux support
- Template / starter commands (`cadtool create --template enclosure`) — planned for v1 once scripting workflow is validated
- Helper library of parametric primitives — planned for v1
- Natural language input to cadtool directly — Claude Code is the NL layer; cadtool takes structured commands only
- FEA, CFD, DFM, or any analysis beyond geometry and rendering
- STEP file as input to `cadtool run` — v2 feature, enables importing manufacturer parts and existing designs
- PyPI / Homebrew distribution
- CLAUDE.md generation — `cadtool context` is the provider-agnostic alternative

---

## Further Research & Reading

The following resources are relevant to where this project intersects with active research, particularly around 3D understanding in multimodal AI models.

### 3D + LLM Research

The question of whether a model can natively "see" 3D geometry (OBJ, STEP, point clouds) the way current models see images is an active and unsolved research area. The current practical approach — rendering multiple views and feeding PNGs to the model — is not a workaround. It is the state of the art, and aligns with the dominant research direction.

- **Awesome-LLM-3D** — the best single curated list of 3D LLM research, actively maintained by Oxford's Active Vision Lab. [github.com/ActiveVisionLab/Awesome-LLM-3D](https://github.com/ActiveVisionLab/Awesome-LLM-3D)
- **"When LLMs Step into the 3D World"** — survey and meta-analysis of 3D tasks via multimodal LLMs. Updated version covers literature through mid-2025. arXiv: 2405.10255
- **ShapeLLM-Omni** — a native 3D LLM that maps 3D objects into discrete latent space tokens, enabling both understanding and generation. arXiv: 2506.01853
- **LLaVA-3D** — multi-view image approach with 3D positional embeddings. Validates the multi-view render strategy cadtool uses.
- **MM-Spatial** (Apple ML Research, ICCV 2025) — exploring 3D spatial understanding in multimodal LLMs
- **"How to Enable LLM with 3D Capacity?"** — Tsinghua / HKUST, IJCAI 2025. Good taxonomy of image-based vs point cloud vs hybrid approaches.

### Labs & Groups to Follow

| Lab | Why |
|-----|-----|
| Oxford Active Vision Lab | Maintaining Awesome-LLM-3D. Most comprehensive tracking of the field. |
| Matthias Nießner (TU Munich) | Consistent output on 3D scene understanding and 3D-LLM benchmarks. |
| Shanghai AI Lab | MMScan and large-scale 3D scene datasets. |
| Apple ML Research | MM-Spatial. Spatial reasoning in vision-language models. |
| Luma AI | Generative 3D, NeRF-based approaches. Commercial product + research. |
| Stability AI | Generative 3D research alongside their image work. |

### Relevant Tools & Projects

| Tool | Notes |
|------|-------|
| CadQuery | Python CAD scripting library wrapping Open CASCADE. The geometry layer for cadtool. [github.com/CadQuery/cadquery](https://github.com/CadQuery/cadquery) |
| ImplicitCAD | Haskell-based CSG modeler with strong CLI story. Worth watching as an alternative kernel. |
| OpenSCAD | Mature parametric CAD scripting tool. Good reference for CLI design patterns. |
| pythonOCC | Python bindings for Open CASCADE. The rendering layer for cadtool v0. |
| trimesh + pyrender | Alternative Python rendering stack. Cleaner API than pythonOCC, more headless setup pain on Linux. Candidate for v1. |
| SimScale | Cloud-based FEA/CFD with an API. Likely first downstream integration candidate. |
| OpenFOAM | Open source CFD. CLI-native and scriptable. Downstream candidate. |

### Honest Assessment

Native 3D understanding in LLMs — where a model reads an OBJ or STEP file the way it reads an image — is probably 2-3 years from being reliable enough to use in an engineering context. The multi-view render approach cadtool uses is not a limitation of the current design. It is the right design for now, and it positions the tool to benefit directly as 3D-native models mature.
