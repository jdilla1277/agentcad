# agentcad

**CAD tool for AI agents.** Give your coding agent the ability to design 3D models.

Your agent writes CadQuery or build123d Python scripts. agentcad handles execution, STEP export, PNG rendering, mesh export (STL/GLB/OBJ), geometric metrics, validation, diffing, and browser preview. All command output is structured JSON.

agentcad is open source under the Apache License 2.0. It runs locally and requires no signup.

[![Featured on Product Hunt](https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1165633&theme=light)](https://www.producthunt.com/products/agentcad?utm_source=badge-featured&utm_medium=badge&utm_campaign=badge-agentcad)

## Demo

[![Watch a coding agent design in agentcad](https://img.youtube.com/vi/Zsn31-IilWM/maxresdefault.jpg)](https://www.youtube.com/watch?v=Zsn31-IilWM)

A coding agent designing in agentcad, live. See more at [agentcad.dev](https://agentcad.dev).

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

- **`agentcad run script.py --output label`** — execute a build123d or CadQuery script, produce versioned STEP file + geometric metrics (volume, dimensions, validity, face/edge counts)
- **`agentcad run ... --preview`** — four-view PNG + turntable GIF for visual verification
- **`agentcad run ... --render iso,front`** — high-quality PNG views
- **`agentcad run ... --export stl,glb`** — mesh export for 3D printing or web viewers
- **`agentcad inspect output.step`** — topology deep-dive (shells, free edges, validity)
- **`agentcad diff 1 2`** — compare versions (metrics, outputs, parameters)
- **`agentcad view output.step`** — open STEP/GLB output in a browser preview viewer
- **`agentcad docs [section]`** — 16 sections of built-in documentation

## No boilerplate

Scripts need zero imports. By default, build123d primitives, `show_object`, and agentcad edit helpers are pre-injected:

```python
box = Box(10, 20, 5)
show_object(box)
```

CadQuery remains supported via `import cadquery as cq`, `agentcad init --runtime cadquery`, or `agentcad run --runtime cadquery`. Run `agentcad docs runtimes` for the dispatch rules.

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

- Python 3.10–3.12 (OpenCascade bindings do not support 3.13+)

## License

Apache License 2.0. See [LICENSE](LICENSE).

## Feedback

If your agent struggles, run `agentcad feedback "what happened"` to capture a friction log.
