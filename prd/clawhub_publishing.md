# Publishing agentcad to ClawHub

Runbook for publishing (or re-publishing) the agentcad skill to [clawhub.ai](https://clawhub.ai/jdilla1277/agentcad). Live listing: https://clawhub.ai/jdilla1277/agentcad

The current flow is **manual** — the sync workflow only updates the public GitHub manifest (`jdilla1277/agentcad-skill`). ClawHub doesn't re-scan on its own; you have to push a new version deliberately. Automating this is tracked in the Friction queue in `roadmap.md`.

## When to republish

Republish ClawHub when any of these change in ways users should see on the listing:

- New agentcad version (`pyproject.toml` bump) with new commands / helpers
- SKILL.md section headers or content renamed (e.g. fixing a false-positive capability tag)
- Frontmatter additions that affect discovery (`homepage`, `emoji`, changed `requires`)

Cosmetic changes inside the SKILL.md body only need the public GitHub manifest to update — no ClawHub republish.

## Prerequisites

- Node.js on your PATH — the ClawHub CLI ships via npm, **not** PyPI. (The `clawhub` package on PyPI is an unrelated library.)
- Install the CLI globally:
  ```bash
  npm i -g clawhub
  clawhub --cli-version   # expect v0.9.0 or later
  ```
- Authenticate:
  ```bash
  clawhub whoami          # should print jdilla1277
  # if not authed: clawhub login   (opens browser OAuth)
  ```

## Step 1 — make sure the public manifest is fresh

Changes to `SKILL_CONTENT` in `app/src/agentcad/commands/skill.py` + push to `main` trigger `.github/workflows/sync-skill.yml`, which regenerates `SKILL.md` in `jdilla1277/agentcad-skill`. Wait for that to finish before publishing.

```bash
gh run list -R jdilla1277/agentcad --workflow=sync-skill.yml --limit 1
# status should be "completed" with conclusion "success"
```

## Step 2 — stage a clean bundle

ClawHub's validator rejects:

- `.git/` — any git internals (auto-flagged as "non-text files")
- `LICENSE` without an extension (detected as binary)
- Anything that isn't plain markdown

Publishing from a raw `git clone` checkout therefore fails. Stage in a scratch directory:

```bash
cd ~/Documents/agentcad-skill
git pull origin main

rm -rf /tmp/agentcad-skill
mkdir /tmp/agentcad-skill
cp SKILL.md README.md /tmp/agentcad-skill/
```

**Folder name = slug.** Keep the scratch dir named `agentcad-skill` — ClawHub derives the slug from the folder name unless you pass `--slug`. Our canonical slug is `agentcad` (matches the `name:` field in SKILL.md) but the folder name controls the initial default.

## Step 3 — publish

```bash
cd /tmp/agentcad-skill
clawhub publish . --version 0.1.5
```

- `--version` is required and must be a valid semver. The CLI doesn't auto-read the version from `SKILL.md` frontmatter — pass it explicitly.
- Other useful flags: `--slug agentcad` (force slug), `--changelog "…"` (release notes), `--tags latest,cad,stable`.

### Known gotchas

- **Spurious rate-limit error.** `GitHub API rate limit exceeded — remaining: 179/180` may fire even though quota is plentiful. Looks like a server-side bug in `clawhub v0.9.0 (41704b85)`. Retry; usually clears within a few attempts.
- **No dry-run flag.** `--dry-run` isn't supported. The first real publish is the validation.
- **Folder name leaks into slug.** Publishing from `/tmp/agentcad-skill-pub` created `agentcad-skill-pub@…`. Always use `agentcad-skill` (or pass `--slug`).

## Step 4 — verify

Open https://clawhub.ai/jdilla1277/agentcad and check:

- **Version badge** matches what you published
- **OpenClaw verdict** is `Benign`. If it's `Suspicious`, check `metadata.openclaw.requires` — overclaiming raises the score.
- **Capability signals** are reasonable. False positives (e.g. `Crypto` because the body had `## Key commands`) are fixed by rewording in `SKILL.md`, letting sync run, then republishing. The tag persists on the live listing until the next republish.
- **VirusTotal scan** completes — can take 5–30 min after publish.

## Frontmatter fields that matter

Required:

- `name` — kebab case, 1–64 chars, `^[a-z0-9]+(-[a-z0-9]+)*$`
- `description` — 1–1024 chars of plain text. **No link rendering in this field.** If you need links, put them in the SKILL.md body.
- `version` — semver (also passed explicitly to `clawhub publish --version`)

Optional, high-leverage for discoverability and trust:

- `homepage` — canonical project page (ours: `https://agentcad.dev`). Appears as a clickable link on the listing. Reduces "unknown new skill" suspicion score.
- `metadata.openclaw.emoji` — small visual identity (e.g. `"📐"`).
- `metadata.openclaw.requires.bins` — required CLI binaries on `$PATH`.
- `metadata.openclaw.requires.anyBins` — OR-group (e.g. any of `python3.12` / `python3.11` / `python3.10`).
- `metadata.openclaw.requires.env` — env vars the skill reads (none for agentcad).
- `metadata.openclaw.primaryEnv` — main credential variable (N/A; we have no API key).
- `metadata.openclaw.files` — glob of bundled scripts if we ever ship executable helpers (not used today).

## Body notes

- Rendered as full markdown on the listing — links, tables, fenced code blocks all work.
- Section headers are scanned for capability signals. Avoid trigger words in headers. We hit this: `## Key commands` → `Crypto` false positive. Neutral headings (`## Commands`, `## Usage`, `## Playbook`) are safe.
- Include install + run examples in the body even though the description field is plain text. Readers land on this page; make them productive.

## TODO — automation

Tracked in `roadmap.md` Friction queue. Minimum viable automation:

1. Extend `.github/workflows/sync-skill.yml` to, after the existing sync step:
   - Check out a scratch dir, copy only `SKILL.md` + `README.md`
   - Install `clawhub` globally (`npm i -g clawhub`)
   - Authenticate with a stored `CLAWHUB_TOKEN` secret (if/when the CLI supports non-interactive auth)
   - Run `clawhub publish . --version $(derive from pyproject)`
2. Gate on a `release: published` trigger — only auto-publish to ClawHub when we cut a new PyPI release, not on every sync.
3. Until the rate-limit guard is fixed upstream, retry with exponential backoff (3 attempts).

If upstream fixes the CLI (+ adds a `publish from remote repo` flow that handles `.git/` stripping), the workflow becomes a one-liner. Until then, the retry-on-rate-limit logic is the pain point.
