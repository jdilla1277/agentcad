# agentcad

agentcad is a local CLI and MCP server for AI-agent CAD workflows.

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
