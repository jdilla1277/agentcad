---
name: agentcad
description: Design, revise, validate, inspect, render, and export parametric 3D CAD models with the open-source AgentCAD CLI in a shell-capable local or Codex Cloud environment. Use for requests to create or edit functional 3D parts or assemblies; do not use for image-only 3D illustrations.
---

# AgentCAD

Use AgentCAD to turn dimensional requirements into reproducible Python source,
validated CAD, measurements, previews, and manufacturing or interchange files.

## Establish the execution environment

1. Work in the location and runtime requested by the user. Explicit user
   instructions override the defaults below.
2. Confirm that a filesystem and shell are available. If they are not, explain
   that this skill needs Work, Codex Cloud, or a connected computer; do not
   pretend that ChatGPT's general-purpose analysis sandbox is a CAD runtime.
3. Run `agentcad context`. If the command is missing or the environment is a
   fresh cloud container, read [references/cloud-environment.md](references/cloud-environment.md)
   and prepare the runtime before continuing.
4. Use Python 3.10, 3.11, or 3.12. Do not try to install AgentCAD on Python
   3.13 or newer.
5. In a cloud or unattended environment, always pass `--no-view`. A localhost
   viewer URL is useful only on the same connected computer.

Keep authored modeling scripts limited to CAD logic. Do not add unrelated
network calls, subprocesses, package installation, or system changes to them.

## Set up the project

- Reuse an existing AgentCAD project when `agentcad context` reports one.
- Otherwise, create a dedicated project directory, add an `agentcad.toml` with
  `build_dir = "./build"` when generated artifacts should stay separate, and
  run `agentcad init --name <project-name>` there.
- Do not initialize over unrelated user files. Use paths returned in AgentCAD's
  JSON responses instead of guessing version or artifact paths.
- Run `agentcad docs quickstart`, followed by the relevant focused section such
  as `agentcad docs parts`, `agentcad docs validation`, or `agentcad docs
  artifacts`, when the task needs more detail.

## Build and verify the model

1. Translate the request into explicit dimensions, constraints, named parts,
   export formats, and acceptance checks. State any necessary assumptions.
2. Write a build123d Python script. AgentCAD injects common primitives and
   `show_object`; explicit imports from `build123d` are also valid. Capture the
   final solid or assembly with `show_object`, `show_assembly`, or
   `show_compound`. Do not manually write the canonical STEP file.
3. Check the script without creating artifacts:

   ```bash
   agentcad run model.py --dry-run --no-daemon
   ```

4. On a cloud Linux worker with Xvfb, run the full visual path:

   ```bash
   xvfb-run -a agentcad run model.py --label first --no-daemon --no-view --render iso,front,top --export stl,glb
   ```

   On a worker without Xvfb or GLX, use the geometry-only fallback and say that
   rendered previews were unavailable:

   ```bash
   agentcad run model.py --label first --no-daemon --no-view --no-preview --no-diff --export stl,glb
   ```

5. Parse the final stdout response as JSON. Keep stderr separate because it
   contains human-readable diagnostics. Never pipe merged output from `2>&1`
   into a JSON parser.
6. Require successful execution and valid geometry. Use the returned STEP path
   with `agentcad measure`, `agentcad inspect`, and, when the request contains
   exact feature requirements, `agentcad check-spec`.
7. Inspect generated preview and render images when visual output is available.
   Revise the source and create a new descriptive label until geometry,
   dimensions, and appearance satisfy the request. Use `agentcad diff` when a
   revision could have changed unintended geometry.

Use `--no-daemon` for cloud and one-shot automation so work stays inside the
active process and display session. Before retrying an unfamiliar or failed
operation, inspect its structured error, `next_actions`, and current context;
do not blindly repeat commands that may have created a version.

## Deliver the result

Return links to the authored Python source, canonical STEP file, requested mesh
exports, previews or renders, and any measurement or specification report.
Summarize the dimensions, validation result, assumptions, and any unavailable
artifact. Do not present a local viewer URL as a remotely accessible link.
