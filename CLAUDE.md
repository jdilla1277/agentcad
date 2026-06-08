# agentcad

agentcad is a local CLI and MCP server for AI-agent CAD workflows.

## Repo Identity

This checkout is the **public** `jdilla1277/agentcad` repo. It is for
externally safe package code, public docs, and examples.

Do **not** open PRs here for internal planning material: PRDs, roadmap notes,
marketing drafts, promotional plans, feedback logs, launch notes, private
operational context, or anything that should not be public.

If the user asks for internal planning, promotion work, or website work for
`agentcad.dev`, use the internal repo at
`/Users/jamesdillard/Documents/mountain-climber-2/agentcad-internal`
(`jdilla1277/agentcad-internal`) instead. Before creating any PR, run
`git remote -v` and confirm whether the target repo is public or internal.

## Development

Use Python 3.10-3.12. CadQuery/OpenCascade does not support Python 3.13+.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[mcp,dev]"
pytest
```

## Product Contract

- Commands return structured JSON.
- `agentcad run` creates versioned output directories and records metadata.
- `agentcad docs` and `agentcad --help` are part of the agent-facing API.
- Keep error messages concise and actionable for coding agents.
- Prefer local, deterministic workflows over hosted dependencies.

## Public Repo Rules

- Do not add internal PRDs, roadmap notes, marketing drafts, feedback logs, secrets, or private operational context.
- Keep examples and docs safe for public users.
- Keep generated artifacts out of git unless they are intentional fixtures.
