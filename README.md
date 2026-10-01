# agentcad

**CAD tool for AI agents.** Give your coding agent the ability to design 3D models.

Your agent writes build123d Python scripts by default. agentcad handles execution, STEP export, PNG rendering, mesh export (STL/GLB/OBJ), geometric metrics, validation, diffing, and browser preview. CadQuery remains available as an explicit compatibility mode. Each command's final response is structured JSON on stdout.

> **Reading the output:** the JSON response is written to **stdout**; human-readable progress and diagnostics go to **stderr**. Parse stdout as JSON and treat stderr as plain text — don't merge the streams with `2>&1` before a JSON parser, or the progress lines will break parsing. If you need both, capture them separately.

agentcad is open source under the Apache License 2.0. It runs locally and requires no signup.

[![Featured on Product Hunt](https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1165633&theme=light)](https://www.producthunt.com/products/agentcad?utm_source=badge-featured&utm_medium=badge&utm_campaign=badge-agentcad)

## Demo

[![Watch a coding agent design in agentcad](https://img.youtube.com/vi/Zsn31-IilWM/maxresdefault.jpg)](https://www.youtube.com/watch?v=Zsn31-IilWM)

A coding agent designing in agentcad, live. See more at [agentcad.dev](https://agentcad.dev).

### Introducing parts

[![Watch agentcad parts rebuild a toy assembly](https://img.youtube.com/vi/VdMhRUiCaNU/maxresdefault.jpg)](https://youtu.be/VdMhRUiCaNU)

Parts let an agent build CAD as named, color-coded pieces and groups, then hand back a viewer a human can inspect. Watch the demo on [YouTube](https://youtu.be/VdMhRUiCaNU) or read the story at [agentcad.dev/parts](https://agentcad.dev/parts).

## Quick start

Install agentcad, then paste this into Claude Code, Cursor, or any coding agent:

```
Create a Python 3.12 virtual environment, then:

pip install agentcad
agentcad skill install
agentcad instructions install
agentcad --help
agentcad init --name phone-stand

Read the --help output — it's your guide to creating, checking, and sharing a model.
Use the default build123d runtime unless the task explicitly requires
CadQuery compatibility.

Then design me a phone stand: a simple angled cradle that holds a phone
at 60 degrees. About 80mm wide, 50mm deep, with a 5mm lip at the bottom
to keep the phone from sliding. Show me a preview when you're done.
```

## What it does

- **`agentcad run script.py --output label`** — execute a build123d script, producing a versioned STEP file + geometric metrics (volume, dimensions, validity, face/edge counts)
- **Automatic review viewer** — successful runs open `viewer.html`; from v2,
  previous and current revisions are preloaded for side-by-side, overlay, and
  Parts-tab change review (`--no-view` opts out)
- **Spatial review comments** — pin human feedback to a surface and named part;
  comments persist locally for the agent's next revision
- **`agentcad run ... --preview`** — four-view PNG for visual verification; the browser viewer can export an on-demand turntable GIF
- **`agentcad run ... --render iso,front`** — high-quality PNG views
- **`agentcad run ... --export stl,glb`** — mesh export for 3D printing or web viewers
- **`agentcad measure output.step`** — dimensional report (overall metrics, edge lengths, face areas, circular/cylindrical diameters)
- **`agentcad check-spec output.step spec.json`** — compare measured cylindrical features against an explicit checklist
- **`agentcad inspect output.step`** — topology deep-dive (shells, free edges, validity)
- **`agentcad parts list REF`** — list named/captured parts for a version
- **`agentcad parts show REF ID`** — show one versioned part by stable id
- **`agentcad parts view REF`** — hand off an isolated, focused, or grouped part review viewer
- **`agentcad review list --status open`** — read shared review threads; humans
  and agents can reply, resolve, and reopen with an auditable history
- **`agentcad diff 1 2`** — compare versions, including actual shared/reference-only/candidate-only source-frame volume for valid closed solids
- **`agentcad view old.step new.step`** — open a synchronized A/B comparison with separate centered projection and source-frame 3D volume artifacts
- **`agentcad docs [section]`** — runtime-aware built-in documentation and worked examples

## Spatial review comments

Viewers opened automatically after a run use a token-protected service bound to
`127.0.0.1`. Press `C`, click the model, confirm the inferred named part and
revision scope, then choose **Send comment**. To hold feedback back, choose
**Save draft** instead; unsent drafts can be edited, deleted, sent individually,
or submitted together with **Send all drafts**.

Comments are plain local JSON under `.agentcad/reviews`—there is no database or
hosted account. On its next turn, an agent can discover and reply to the review:

```bash
agentcad context
agentcad review list --status open
agentcad review comment --message "Could this rib be thinner?" --part support_rib --version current
agentcad review reply C1 --message "Increased the clearance to 4 mm." --version current
agentcad review resolve C1 --message "Implemented in the current revision." --version current
```

Agents can also initiate an immediately-open thread on a named part. Add
`--scope previous|both` to target comparison sides or `--point-mm X,Y,Z` to
place its pin more precisely. Humans and agents can both reply, resolve, or reopen. Every action records its
actor, timestamp, optional message, and associated revision. The older
`mark-addressed` command remains available for compatibility.

## No boilerplate

Scripts need zero imports. By default, build123d primitives, `show_object`, and agentcad edit helpers are pre-injected:

```python
box = Box(10, 20, 5)
show_object(box)
```

`agentcad init` records build123d as the project runtime. That keeps the
script API, built-in docs, and subsequent runs on one clear default.

## CadQuery compatibility

CadQuery remains supported for existing scripts and projects, but it is not
the default authoring path.

For a CadQuery project:

```bash
agentcad init --name legacy-model --runtime cadquery
agentcad docs quickstart --runtime cadquery
agentcad run script.py --output first
```

For a one-off CadQuery script inside a build123d project:

```bash
agentcad docs preamble --runtime cadquery
agentcad run legacy.py --output legacy --runtime cadquery
```

Keep each script on one CAD API. If a script clearly targets the other engine,
agentcad reports the mismatch and the exact one-off override. Run
`agentcad docs runtimes` for the complete dispatch contract.

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
