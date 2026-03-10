# cadtool

A CLI CAD tool designed for AI agents. Describe what you want, your agent builds it.

## For Humans

**1. Install cadtool**

```bash
mkdir myproject && cd myproject
python3.12 -m venv .venv && source .venv/bin/activate
pip install git+https://github.com/jdilla1277/mountain-climber.git#subdirectory=app
```

> **Don't have Python 3.12?** `brew install python@3.12` (macOS) or `sudo apt install python3.12 python3.12-venv` (Ubuntu). CadQuery requires 3.10-3.12 — 3.13+ won't work.

**2. Paste this prompt into Claude Code, Codex, or any coding agent:**

```
cadtool is installed in this environment. It is a CLI CAD tool that lets you
create 3D models by writing CadQuery Python scripts.

To get started:
1. Run: cadtool --help
   Read the full output carefully — it contains the operational briefing with
   commands, script-writing patterns, response schema, metrics, and debugging.
2. Run: cadtool init --name <project_name>
3. Write a CadQuery script and run it:
   cadtool run script.py --output <label> --render iso --preview

cadtool returns structured JSON from every command.
Run 'cadtool docs' for deep-dive documentation (15 sections).

Now, please build me: <describe what you want>
```

That's it. The agent reads `--help`, initializes the project, and starts building. Runs best in skip-permissions mode.

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
