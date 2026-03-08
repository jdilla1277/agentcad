# Friction Log — 2026-03-07

**Tester:** Claude Code (AI agent)
**Task:** Activate venv, install cadtool, explore and exercise the CLI
**Project state:** Milestone 1 complete (cadtool init)

---

## Summary

cadtool is in early scaffolding. The `init` command works correctly and the JSON output contract is solid. The main friction points are around onboarding (no setup instructions until CLAUDE.md was updated mid-session) and the gap between the current feature set and what the PRD describes. Below are the specific moments of friction I hit, in order.

---

## Friction Points

### 1. No setup instructions on first encounter

**Severity:** Medium
**What happened:** I had to reverse-engineer the setup by finding the `.venv` directory and `pyproject.toml` myself. There was no README, no setup section in CLAUDE.md, and no `Makefile` or script to bootstrap the environment.
**What I expected:** A single command or clear instruction to get from clone to working CLI.
**Resolution:** CLAUDE.md was updated during the session to include setup instructions. This is now resolved.
**Suggestion:** Consider a `make setup` or `./scripts/bootstrap.sh` for one-command setup, especially since future milestones will add heavier dependencies (CadQuery, pythonOCC).

### 2. `cadtool` with no arguments exits with code 2 (not 0)

**Severity:** Low
**What happened:** Running `cadtool` bare (no subcommand) returns exit code 2. The help text is shown, which is fine, but for an agent-first tool, the exit code matters. An agent checking `exit_code != 0` would interpret this as an error.
**What I expected:** Either exit code 0 with help text, or structured JSON telling me what commands are available.
**Context:** This is default Click behavior for a group with no default command. It's not a bug, but it's worth a deliberate decision: should the bare `cadtool` command return JSON with available commands? The PRD's `cadtool context` command might serve this role once implemented.

### 3. Manifest schema doesn't match the PRD

**Severity:** Medium
**What happened:** `cadtool init` creates a manifest with `{"name", "version", "created"}`. The PRD spec for `cadtool.json` shows `{"project", "created", "current", "versions": [...]}`. The field is `name` in the code but `project` in the PRD. There's no `versions` array or `current` pointer.
**What I expected:** Either the manifest matches the PRD schema, or there's a note explaining the deviation.
**Suggestion:** This will naturally get addressed in Milestone 2 when versioning lands, but the `name` vs `project` key discrepancy should be resolved now to avoid a breaking change later.

### 4. No `cadtool context` or `cadtool docs` yet

**Severity:** Low (expected at this stage)
**What happened:** As an agent, my first instinct was to run `cadtool context` to orient myself. That command doesn't exist yet.
**What I expected:** N/A — the PRD explicitly schedules this for Milestone 7.
**Why it matters for the friction log:** This is the most important command for the stated goal of "best CAD tool for agents." Every agent session will start with `cadtool context`. Consider pulling a minimal version of it earlier in the roadmap — even a stub that returns `{"commands": ["init"], "project": null}` would be valuable.

### 5. JSON output schema is inconsistent with PRD

**Severity:** Low
**What happened:** The `init` command returns `{"command": "init", "status": "success", "project": "name"}`. The PRD's output schema example shows fields like `version`, `label`, `instruction`, `description`, `outputs`. These are run-specific, but there's no documented schema for non-run commands.
**What I expected:** A documented response schema for each command, or a base schema that all commands share.
**Suggestion:** Define the base response schema now: `{"command", "status", "message?", ...}` as a contract that every command adheres to. This prevents drift as more commands are added.

### 6. Error output on already-initialized directory doesn't include project name

**Severity:** Low
**What happened:** Running `cadtool init` twice returns `{"command": "init", "status": "error", "message": "cadtool.json already exists"}`. It doesn't tell me *which* project already exists there.
**What I expected:** Something like `{"command": "init", "status": "error", "message": "cadtool.json already exists", "project": "my-project"}` — read the existing manifest and include context.
**Why it matters:** An agent hitting this error has to do a separate file read to figure out what project is there. A small quality-of-life improvement.

---

## What's Working Well

- **JSON-first output is great.** Every response parses cleanly. No prose to scrape. This is the right foundation for an agent tool.
- **Click is a good choice.** Clean `--help` output, option parsing works, exit codes are standard. It'll scale well through the milestones.
- **Test coverage is solid for M1.** 12 tests, all passing, covering happy path, error case, schema validation, and output format. The `isolated_dir` fixture pattern is clean.
- **src layout is correct.** Editable install works, import paths are clean, no namespace issues.
- **The PRD is exceptionally well-written.** Clear scope, honest about limitations, good research section. The implementation plan maps directly to the PRD with no ambiguity.

---

## Recommendations (Prioritized)

1. **Resolve `name` vs `project` key in manifest** — do this before M2 to avoid a migration
2. **Pull a minimal `cadtool context` into M1 or early M2** — it's the agent's front door
3. **Define base response schema** — `{"command", "status"}` minimum, documented once, enforced everywhere
4. **Decide on bare `cadtool` behavior** — exit 0 with JSON, or keep Click's default exit 2
5. **Add a bootstrap script** — setup will get harder once CadQuery/pythonOCC are dependencies
