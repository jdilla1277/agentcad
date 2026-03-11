# Mountain Climber

A CLI-based CAD tool designed for AI agents. The goal is to build the best CAD tool in the world for agents — issue commands, get back structured output to design CAD drawings.

## Project Structure

- `/prd` — Product requirements documents
- `/app` — Application code

## Setup

Before running `cadtool` or `pytest`, activate the virtual environment:

    source app/.venv/bin/activate

The package is installed in editable mode inside that venv. If the venv doesn't exist, create it with **Python 3.12** (CadQuery/OCP requires 3.12 or earlier):

    /opt/homebrew/bin/python3.12 -m venv app/.venv
    source app/.venv/bin/activate
    pip install -e app/ pytest

## Development Process

- **Red/Green TDD** — Always write a failing test first (red), then write the minimal code to make it pass (green). Refactor only after green.
- **Work in branches** — Do all work in feature branches off `main`. Never commit directly to `main`.

## PR Retro

After each PR, ask: what would have made this easier? Consider:
- Tech debt to clean up
- Guardrails that would catch errors sooner
- Documentation gaps
- Anything to improve the dev experience

## PRD Structure

- `prd/v0_implementation_plan.md` — master milestone tracker
- `prd/m{N}_plan.md` — per-milestone detailed plans
- `prd/cadtool_scope.md` — v0 scope document

## Agent Friction Testing

To test cadtool as an agent would actually experience it (no repo access, no source code), install from pip in a fresh directory:

    mkdir /tmp/cadtool-test && cd /tmp/cadtool-test
    python3.12 -m venv .venv && source .venv/bin/activate
    pip install git+https://github.com/jdilla1277/mountain-climber.git#subdirectory=app

Then start Claude Code (or any agent) in that directory. The agent only has the `cadtool` CLI — no source, tests, or PRDs to read.

## Friction Log Philosophy

Every friction point in the stack is our problem. If an agent hits a CadQuery footgun, a confusing OCP API, or an unintuitive workplane behavior — that's a cadtool problem. We own the experience end-to-end. Fix it with better docs, helpers, wrappers, or guardrails. There is no "upstream issue, not our bug."

## Notes

This file should be updated as the project evolves. Keep it current with new conventions, decisions, and patterns as they emerge.
