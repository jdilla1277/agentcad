# Cloud environment setup

Read this reference when AgentCAD is missing, when preparing a Codex Cloud
environment, or when rendering fails on headless Linux.

## What the plugin supplies

This is a skills-only plugin. It teaches the agent how to use the open-source
AgentCAD CLI, but it does not bundle Python, OpenCascade, a persistent machine,
or a hosted MCP server. The execution environment supplies those resources.
Codex Cloud can run them for the duration of a task, and a published environment
snapshot can make later tasks start faster. Treat every task as isolated; do
not rely on a daemon, display server, or localhost URL surviving between tasks.

## Install AgentCAD

First select an available Python version in the supported range:

```bash
python3.12 --version
python3.11 --version
python3.10 --version
```

Create a project-local virtual environment with the first command that exists.
For a normal project using the published package:

```bash
python3.12 -m venv .venv-agentcad
.venv-agentcad/bin/python -m pip install --upgrade pip
.venv-agentcad/bin/python -m pip install agentcad
```

When the active repository is the AgentCAD source repository, install that
checkout so the plugin and runtime are tested together:

```bash
python3.12 -m venv .venv-agentcad
.venv-agentcad/bin/python -m pip install --upgrade pip
.venv-agentcad/bin/python -m pip install -e .
```

Use the corresponding Python executable when 3.11 or 3.10 was selected. Add
`.venv-agentcad/bin` to the environment's `PATH`, or invoke the CLI as
`.venv-agentcad/bin/agentcad`.

For repeat use in Codex Cloud, put these commands in the environment setup
script and publish the environment after a successful setup. Allow access to
PyPI and to the operating-system package repository if the network policy uses
an allowlist.

## Enable headless previews and renders

AgentCAD's OpenCascade renderer needs an X11/GLX display. On an Ubuntu or
Debian-based image, install Xvfb in the environment setup step if package
installation is permitted:

```bash
sudo apt-get update
sudo apt-get install -y xvfb
command -v xvfb-run
```

Run the CLI inside the temporary display and keep browser launching disabled:

```bash
xvfb-run -a agentcad run model.py --label first --no-daemon --no-view --render iso,front,top
```

Passing `--no-daemon` is important: a background process must not outlive the
temporary X display created by `xvfb-run`.

If installing Xvfb is unavailable or the image lacks working GLX libraries,
continue with geometry, STEP validation, measurement, inspection, and mesh
exports:

```bash
agentcad run model.py --label first --no-daemon --no-view --no-preview --no-diff --export stl,glb
```

Report that preview rendering was skipped; do not describe the geometry-only
fallback as full visual verification.

## Smoke test

Run these checks after setup:

```bash
agentcad --help
agentcad context
agentcad docs quickstart
```

Then initialize a dedicated test project, create a minimal script containing
`show_object(Box(10, 20, 5))`, run `agentcad run model.py --label smoke
--no-daemon --no-view`, and confirm that the JSON response reports valid
geometry and a STEP output path. The `agentcad context` response includes the
installed `tool_version`.
