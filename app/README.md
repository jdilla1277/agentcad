# agentcad

**CAD tool for AI agents.** Give your coding agent the ability to design 3D models.

Your agent writes CadQuery Python scripts. agentcad handles execution, STEP export, PNG rendering, mesh export (STL/GLB/OBJ), geometric metrics, and validation. All output is structured JSON.

## Quick start

Install agentcad, then paste this into Claude Code, Cursor, or any coding agent:

```
Create a Python 3.12 virtual environment, then:

pip install agentcad
agentcad skill install
agentcad --help

Read the --help output — it's your operational briefing.

Then design me a phone stand: a simple angled cradle that holds a phone
at 60 degrees. About 80mm wide, 50mm deep, with a 5mm lip at the bottom
to keep the phone from sliding. Show me a preview when you're done.
```

## What it does

- **`agentcad run script.py --output label`** — execute a CadQuery script, produce versioned STEP file + geometric metrics (volume, dimensions, validity, face/edge counts)
- **`agentcad run ... --preview`** — quick 256x256 iso PNG for visual verification
- **`agentcad run ... --render iso,front`** — high-quality PNG views
- **`agentcad run ... --export stl,glb`** — mesh export for 3D printing or web viewers
- **`agentcad inspect output.step`** — topology deep-dive (shells, free edges, validity)
- **`agentcad diff 1 2`** — compare versions (metrics, outputs, parameters)
- **`agentcad docs [section]`** — 16 sections of built-in documentation

## No boilerplate

Scripts need zero imports. `cq`, `show_object`, and 16 geometry helpers are pre-injected:

```python
box = cq.Workplane('XY').box(10, 20, 5)
show_object(box)
```

Helpers include `translate`, `rotate`, `mirror_fuse`, `loft_sections`, `tapered_sweep`, `involute_gear_profile`, and more. Run `agentcad docs helpers` for the full list.

## MCP integration

For native tool integration with Claude Code, Cursor, or Windsurf:

```bash
pip install agentcad[mcp]
```

Add to `.mcp.json`:

```json
{"agentcad": {"command": "python", "args": ["-m", "agentcad.mcp"]}}
```

## Requirements

- Python 3.10–3.12 (CadQuery/OpenCascade does not support 3.13+)

## Feedback

If your agent struggles, run `agentcad feedback "what happened"` to capture a friction log.
