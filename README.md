# cadtool

A CLI CAD tool designed for AI agents. Describe what you want, your agent builds it.

## For Humans

Copy and paste this prompt into Claude Code, Codex, or any coding agent. Runs best in skip-permissions mode.

```
Install cadtool. It is a CLI CAD tool that lets you create 3D models by writing
CadQuery Python scripts.

1. Create a fresh directory and set up a Python 3.12 virtual environment.
   CadQuery requires Python 3.10-3.12 — 3.13+ won't work.
2. Install cadtool:
   pip install git+https://github.com/jdilla1277/mountain-climber.git#subdirectory=app
3. Run: cadtool --help
   Read the full output — it is your operational briefing with commands,
   script-writing patterns, response schema, metrics, and debugging.
4. Initialize a project: cadtool init --name <project_name>

Once you've done that, please build me:
```

## For Agents

cadtool is a CLI CAD tool. Start here:

```
cadtool --help
```

That output is your complete operational briefing — commands, script-writing patterns, response schema, metrics, and debugging. Everything you need is in there.

## Development

```bash
/opt/homebrew/bin/python3.12 -m venv app/.venv
source app/.venv/bin/activate
pip install -e app/ pytest
pytest app/tests/ -v
```
