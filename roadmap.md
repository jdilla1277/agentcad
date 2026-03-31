# cadtool — Roadmap

Single source of truth for all milestones. For per-milestone detailed plans, see `prd/m{N}_plan.md`.

---

## Summary

| # | Milestone | Size | Status |
|---|-----------|------|--------|
| | **v0.1 — Core Pipeline** | | |
| M1 | Project scaffolding & `cadtool init` | S | Done |
| M2 | 2D geometry primitives | S | Done |
| M3 | Geometry enrichment | S | Done |
| M4 | `cadtool run` — script execution & STEP export | L | Done |
| M5 | Error handling & failed versions | S | Done |
| M6 | PNG rendering | M | Done |
| M7 | GLB & STL export | M | Done |
| M8 | `cadtool render` (custom views, `--zoom`) | M | Done |
| ~~M9~~ | ~~2D primitive cleanup~~ (absorbed into M4) | — | Done |
| M9b | `--focus` for `cadtool render` | S | Done |
| M10 | `cadtool context`, `cadtool docs` & `cadtool diff` | M | Done |
| M11 | Geometry helpers (`loft_sections`, `tapered_sweep`, `naca_wire`, `mirror_fuse`) | M | Done |
| M12 | `cadtool export` command | S | Done |
| M13 | OBJ export & end-to-end verification | S | Done |
| | | | |
| | **v0.2 — Fast Loop & Friction Fixes** | | |
| M14 | Geometric metrics in build output | S | Done |
| M15 | Script preamble — implicit runtime context | S | Done |
| M16 | Pre-execution validation | S | Done |
| M17 | Friction fixes — auto-compound, docs improvements | S | Done |
| M18 | Quick preview mode (`--preview`) | S | Done |
| M19 | Multi-solid colored GLB export | S | Done |
| M20 | Patterns docs, positioning helpers & `cadtool view` | M | Done |
| M21 | Parametric scripts (`--params`) | M | Done |
| M22 | Friction fixes — Python version check, rotate docs, dry-run | S | Done |
| M23 | Persistent worker / daemon mode | M | Done |
| M24 | Friction fixes — `cadtool inspect`, render quality, bug fixes | M | Done |
| M25 | `--help` operational briefing & README | S | Done |
| M26 | Performance warnings in docs | XS | Done |
| M27 | Friendlier `cadtool init` when project exists | XS | Done |
| M28 | Better Python version / import error diagnostics | S | Done |
| M29 | Progress indicator for long `cadtool run` | M | Done |
| M30 | `involute_gear_profile` helper | M | Done |
| M31 | User-specified part colors in GLB export | S | Done |
| M32 | File paths in `cadtool context` | XS | Done |
| M33 | Per-part design workflow (named parts, per-part metrics/renders) | L | Done |
| M34 | Assembly positioning helpers (`bbox_point`, `place_at`, `assemble`) | S | Done |
| M35 | Wire helpers & `elliptical_sweep` | S | Done |
| M36 | Validity warnings & diagnostics | S | Pending |
| M37 | Wire helper robustness (point dedup) | S | Pending |
| M38 | Complex profile patterns in docs | S | Pending |
| | | | |
| | **v0.3 — Distribution & Agent Ecosystem** | | |
| M39 | Openness & licensing strategy | S | Pending |
| M40 | Publish to PyPI | S | Pending |
| M41 | MCP server | M | Pending |
| M42 | ClawHub skill (OpenClaw) | XS | Pending |
| M43 | Claude Code skill/plugin | XS | Pending |
| M44 | Homepage | M | Pending |
| M45 | Public GitHub repo | S | Pending |
| M46 | Alpha users (5–10 testers, collect feedback) | M | Pending |
| M47 | Launch — Product Hunt, Hacker News, community posts | S | Pending |

---

## Where we are

**v0.1 (Core Pipeline):** Done. cadtool can execute CadQuery scripts, render PNGs, export to STEP/GLB/STL/OBJ, diff versions, and show docs. 368 tests, 10 commands.

**v0.2 (Fast Loop & Friction Fixes):** In progress. Metrics, preamble, validation, daemon, parametric scripts, inspect, per-part workflow, assembly helpers, wire helpers shipped. Remaining: validity warnings (M36), wire helper robustness (M37), complex profile docs (M38) — driven by spur gear friction log.

**v0.3 (Distribution & Agent Ecosystem):** Not started. Make cadtool installable by anyone and discoverable by every major AI agent platform.

---

## v0.1 — Core Pipeline

### M1: Project Scaffolding & `cadtool init` ✓

**Goal:** A pip-installable CLI that runs `cadtool init` and produces correct project structure.

**Delivered:** 12 tests, `cadtool init` command with JSON output, Click CLI, pytest suite.

---

### M2: 2D Geometry Primitives ✓

**Goal:** Let agents create and inspect geometry via `add-rect`, `add-circle`, and `list`.

**Delivered:** 38 tests, 4 commands (`init`, `add-rect`, `add-circle`, `list`), shared manifest module, auto-incrementing IDs.

---

### M3: Geometry Enrichment ✓

**Goal:** Labels, count, delete, and get — close the CRUD gaps before moving to 3D.

**Delivered:** 58 tests, 6 commands, optional `--label` on shapes, `count` in list response.

---

### M4: `cadtool run` — Script Execution & STEP Export ✓

**Goal:** Execute a CadQuery script and produce a versioned STEP file.

**Delivered:** 25 tests, 2 commands (`init`, `run`). CadQuery 2.7.0 via CQGI, STEP export, versioned directories, meta.json, manifest tracking. 2D primitives removed (M9 absorbed).

---

### M5: Error Handling & Failed Versions ✓

**Goal:** Failed runs are preserved with `_failed` suffix, error details in JSON.

**Delivered:** 30 tests. Script failures create `_failed` directories with `script.py` + `meta.json`, tracked in manifest with `status: "failed"`. Failed runs consume version numbers; `current` does not advance.

---

### M6: PNG Rendering ✓

**Goal:** Produce PNG renders from CadQuery results for agent visual inspection.

**Delivered:** 43 tests. OCP offscreen rendering via `V3d_View.ToPixMap`. `--render` option on `cadtool run` accepts comma-separated views. 7 views available; `all` expands to front, right, top, iso.

---

### M7: GLB & STL Export ✓

**Goal:** Mesh exports for web viewers, 3D printing, and agent interop.

**Delivered:** 53 tests. `--export stl,glb` option on `cadtool run`. STL via CadQuery exporters. GLB via OCP `RWGltf_CafWriter`.

---

### M8: `cadtool render` ✓

**Goal:** Render additional views of an existing STEP file without creating a new version.

**Delivered:** 74 tests, 3 commands. `cadtool render <step_path> --view <spec>` with named views, custom angles, `--zoom`, `--name`.

---

### ~~M9: 2D Primitive Cleanup~~ ✓ (absorbed into M4)

---

### M9b: `--focus` for `cadtool render` ✓

**Goal:** Make `cadtool render` zoom usable for detail inspection.

**Delivered:** 84 tests. `--focus x,y,z` sets camera target. `--no-fit` skips `FitAll()`. `_apply_camera()` helper extracted.

---

### M10: `cadtool context`, `cadtool docs` & `cadtool diff` ✓

**Goal:** Agent discoverability and version comparison commands.

**Delivered:** 111 tests, 6 commands. `cadtool context` returns project state. `cadtool docs [section]` returns hardcoded documentation. `cadtool diff <ref1> <ref2>` compares versions.

---

### M11: Geometry Helpers ✓

**Goal:** Reusable organic geometry primitives for CadQuery scripts.

**Delivered:** `cadtool.helpers` module with `loft_sections`, `tapered_sweep`, `naca_wire`, `mirror_fuse`.

---

### M12: `cadtool export` Command ✓

**Goal:** Post-hoc mesh export from existing STEP files without re-running scripts.

**Delivered:** 141 tests, 7 commands. `cadtool export <step_file> --format stl,glb`.

---

### M13: OBJ Export & End-to-End Verification ✓

**Goal:** OBJ export format and end-to-end workflow test.

**Delivered:** 149 tests. `export_obj()` via manual triangle extraction. End-to-end workflow test covers init → run → render → export → context → diff.

---

## v0.2 — Fast Loop & Friction Fixes

Driven by agent friction testing across 6+ real model sessions.

**Friction log sources:**
- Helical gear (2026-03-10)
- Landing gear (2026-03-09)
- Discovery (2026-03-09)
- B-2 Spirit (2026-03-08)
- Golden Gate Bridge (2026-03-08)
- Desk lamp
- Eiffel Tower (2026-03-10)
- Spur gear (2026-03-31)

---

### M14: Geometric Metrics in Build Output ✓

**Goal:** Every successful `cadtool run` returns geometric metrics (bbox, volume, area, face/edge counts, validity) so agents can verify shape correctness without rendering.

---

### M15: Script Preamble ✓

**Goal:** Scripts run with `cq`, `show_object`, and all helpers pre-injected. Agent writes pure design intent, zero import boilerplate.

---

### M16: Pre-Execution Validation ✓

**Goal:** Catch preventable mistakes (syntax errors, missing `show_object()`, bad imports) in <100ms before the expensive build, without consuming a version number.

---

### M17: Friction Fixes — Auto-Compound & Docs ✓

**Goal:** Fix silent data loss from multiple `show_object()` calls and fill documentation gaps.

**Delivered:** 202 tests. Auto-compound, quickstart docs, units note, tapered_sweep limitation docs, type conversion patterns.

---

### M18: Quick Preview Mode ✓

**Goal:** `--preview` flag on `cadtool run` produces a fast 256x256 iso PNG for shape verification during iteration.

**Delivered:** 210 tests. Preview path in output JSON and meta.json. Coexists with `--render`.

---

### M19: Multi-Solid Colored GLB Export ✓

**Goal:** GLB exports preserve individual solids with per-part colors.

**Delivered:** 215 tests. `export_glb()` decomposes compounds, assigns colors from 10-color palette via `XCAFDoc_ColorTool`.

---

### M20: Patterns Docs, Positioning Helpers & `cadtool view` ✓

**Goal:** Eliminate positioning footguns, make GLB viewable, allow mixed view specs.

**Delivered:** 234 tests, 8 commands. `cadtool docs patterns`, `translate()` + `rotate()` helpers, `cadtool view <file>`, mixed view specs (`front,right,45:15`).

---

### M21: Parametric Scripts ✓

**Goal:** `--params key=val,key=val` on `cadtool run` passes parameter overrides to CQGI.

**Delivered:** 247 tests. Type coercion, unknown param detection, params in output JSON, `cadtool diff` shows param changes.

---

### M22: Friction Fixes — Desk Lamp ✓

**Goal:** Fix P0/P1 issues from desk lamp friction test.

**Delivered:** 254 tests. Python version check, `rotate()` direction docs, `--dry-run` mode, angled positioning example.

---

### M23: Persistent Worker (Daemon Mode) ✓

**Goal:** Background process keeps CadQuery/OCP loaded in memory, eliminating 3-5s cold start.

**Delivered:** ~282 tests, 9 commands. `cadtool daemon start/stop/status`. Unix domain socket IPC, eager module warm-up, auto-routing from `cadtool run`.

---

### M24: Friction Fixes — Helical Gear ✓

**Goal:** Fix geometry debugging gaps from helical gear friction test.

**Delivered:** ~304 tests, 10 commands. `cadtool inspect`, validity diagnostics, negative volume warning, brighter renders, relative path support, version dir collision fix, custom angles in `--render`.

---

### M25: `--help` Operational Briefing & README

**Goal:** Make `cadtool --help` a complete operational briefing so an agent can be productive on first contact.

**Status:** In Progress (PR #15)

---

### M26: Performance Warnings in Docs

**Goal:** Document the twistExtrude + spline performance cliff so agents don't burn timeouts.

**Scope:** Add warning to `cadtool docs patterns`. 1 test, 1 docs string edit.

**Status:** Pending

---

### M27: Friendlier `cadtool init` Error ✓

**Goal:** When project already initialized, tell the agent to run `cadtool context`.

---

### M28: Better Python Version / Import Error Diagnostics ✓

**Goal:** When CadQuery/OCP imports fail due to wrong Python version, tell the agent why.

---

### M29: Progress Indicator for Long Runs ✓

**Goal:** Periodic progress heartbeats to stderr during long `cadtool run` executions.

---

### M30: `involute_gear_profile` Helper ✓

**Goal:** `involute_gear_profile(module, teeth, pressure_angle)` returns a closed TopoDS_Wire of an involute spur gear profile.

**Delivered:** 8 new tests. Full involute curve generation with root/tip arcs. Added to preamble and docs.

---

### M31: User-Specified Part Colors in GLB Export ✓

**Goal:** Let scripts assign colors to parts via `show_object()` options. Falls back to auto-palette.

---

### M32: File Paths in `cadtool context` ✓

**Goal:** Each version in `cadtool context` output includes its file paths.

---

### M33: Per-Part Design Workflow ✓

**Goal:** Named parts with per-part metrics, renders, and preview.

**Delivered:** `_extract_parts()`, `_COLOR_MAP`, per-part metrics/renders/preview, `parts/` subdir with `{name}_{view}.png` naming.

---

### M34: Assembly Positioning Helpers ✓

**Goal:** Eliminate manual coordinate math for multi-part models.

**Delivered:** `bbox_point(shape, x, y, z)`, `place_at(shape, from_pt, to_pt)`, `assemble(*shapes)`. Added to preamble, docs, and patterns.

---

### M35: Wire Helpers & `elliptical_sweep` ✓

**Goal:** Reusable wire primitives and variable-cross-section sweep for organic shapes.

**Delivered:** `ellipse_wire`, `spline_wire`, `polygon_wire`, `rounded_rect_wire`, `elliptical_sweep`. All return `TopoDS_Wire` or `TopoDS_Solid`. Added to preamble and docs.

---

### M36: Validity Warnings & Diagnostics

**Goal:** Make geometry failures loud and actionable instead of buried in metrics JSON.

**Scope:**
- Surface `is_valid: false` as a top-level `warning` in `cadtool run` output JSON (not just in `metrics`).
- Improve negative volume warning: include "check wire winding order" guidance instead of generic "shape may have inverted normals."
- Catch `BRep_API: command not done` and add context about wire closure / endpoint tolerance.

**Motivation:** Spur gear friction log — agent got `status: success` with broken geometry across 5 versions because `is_valid` was only in the metrics sub-object.

**Status:** Pending

---

### M37: Wire Helper Robustness (Point Dedup)

**Goal:** Prevent `Knots interval values too close` and `BSplCLib::Interpolate` errors in wire helpers.

**Scope:**
- Add point deduplication within tolerance (e.g., 1e-6mm) to `spline_wire` before passing points to `GeomAPI_PointsToBSpline`.
- Same dedup for `polygon_wire` before building line edges.
- Tests: near-duplicate points at segment boundaries should be silently collapsed.

**Motivation:** Spur gear friction log — involute-to-arc transition points are theoretically coincident but numerically distinct, causing OCC interpolation failures.

**Status:** Pending

---

### M38: Complex Profile Patterns in Docs

**Goal:** Document construction strategies that avoid the most common BRep footguns for complex profiles.

**Scope:**
- "Cut from blank" pattern: subtractive construction (cut gaps from a cylinder) vs. additive (build teeth up). Explain why subtraction inherits correct normals and avoids self-intersection.
- Wire winding direction: right-hand rule, how CW vs. CCW affects face normals, how to diagnose via negative volume.
- Mixed edge type wires: combining line edges, circular arcs, and BSplines into a single `BRepBuilderAPI_MakeWire`.
- Add to `cadtool docs patterns`.

**Motivation:** Spur gear friction log — the key insight that unlocked a valid gear was inverting construction from additive to subtractive.

**Status:** Pending

---

## v0.3 — Distribution & Agent Ecosystem

**Goal:** Make cadtool installable by anyone and discoverable by every major AI agent platform.

**Reference implementations:** [Stripe Projects](https://projects.dev/) (CLI-first, agent-friendly provisioning), [RAMP CLI](https://github.com/ramp-public/ramp-cli) (`--agent` flag, `ramp skills` command, separate MCP server).

**Suggested order:** M45 (public repo) → M40 (PyPI) → M41 (MCP) → M43 (Claude skill) → M42 (ClawHub) → M44 (homepage)

---

### M39: Openness & Licensing Strategy

**Goal:** Decide how open cadtool will be — public vs. private repo, open-source vs. source-available vs. proprietary, license choice — and document the rationale.

**Scope:**
- Decide: public GitHub repo or private with pip-installable distribution only?
- Choose a license (MIT, Apache 2.0, BSL, proprietary, etc.) or explicitly defer.
- Consider implications for each distribution channel (PyPI, MCP, ClawHub, skills).
- Document the decision and reasoning in this roadmap or a dedicated doc.
- Gates M45 (public repo) — can't open the repo without deciding what "open" means.

**Status:** Pending

---

### M40: Publish to PyPI

**Goal:** `pip install cadtool` (or `pipx install cadtool`) works for anyone.

**Scope:**
- Register "cadtool" on PyPI (or alternative name if taken).
- Add metadata to `pyproject.toml`: description, authors, readme, urls, classifiers.
- Test publish on TestPyPI first.
- Set up GitHub Actions for automated publishing via PyPI Trusted Publishers (OIDC, no API tokens).
- Version bump to 0.2.0 for first public release.

**Status:** Pending

---

### M41: MCP Server

**Goal:** A Model Context Protocol server that exposes cadtool commands as native agent tools. Works with Claude Code, Cursor, Windsurf, VS Code Copilot, and any MCP-compatible agent.

**Scope:**
- New module `cadtool/mcp_server.py` using the `mcp` Python SDK (`FastMCP`).
- Expose tools: `run`, `render`, `export`, `inspect`, `docs`, `context`, `diff`, `view`.
- stdio transport (client spawns the process).
- Users configure via `.mcp.json`: `{"cadtool": {"command": "python", "args": ["-m", "cadtool.mcp"]}}`.
- Structured JSON responses (cadtool already returns these — thin wrapper).
- Add `mcp` to optional dependencies in `pyproject.toml`.
- Tests for tool registration and response format.

**Status:** Pending

---

### M42: ClawHub Skill (OpenClaw)

**Goal:** Publish a cadtool skill to ClawHub so OpenClaw agents can discover and use cadtool.

**Scope:**
- Write `SKILL.md` with frontmatter: name, description, `requires.bins: ["cadtool"]`.
- Cover all commands, helpers, and workflow in the skill body.
- Publish via `clawhub publish`.
- Verify discoverability via `clawhub search "CAD"`.

**Status:** Pending

---

### M43: Claude Code Skill/Plugin

**Goal:** Claude Code users can install cadtool agent instructions with one command.

**Scope:**
- Create `.claude/skills/cadtool/SKILL.md` in the repo (auto-discovered for anyone who clones).
- Optionally: package as a Claude Code plugin for `/plugin install` distribution.
- Skill covers: commands, helpers, preamble, workflow, common patterns.
- `allowed-tools: Bash(cadtool *)` for auto-approval.

**Status:** Pending

---

### M44: Homepage

**Goal:** A public website (e.g., cadtool.dev) that explains what cadtool is, shows examples, and links to install instructions for every agent platform.

**Scope:**
- Landing page: hero, value prop, install snippet, demo GIF/video.
- Per-platform setup guides: Claude Code, Cursor, Windsurf, OpenClaw, Codex.
- Link to GitHub, PyPI, ClawHub.
- Static site (GitHub Pages, Vercel, or Cloudflare Pages).

**Status:** Pending

---

### M45: Public GitHub Repo

**Goal:** Make the repository public and ready for external visitors.

**Scope:**
- Audit repo for secrets, credentials, internal paths.
- Write a public-facing README: what it is, install, agent setup, examples.
- Add contributing guidelines (even if minimal — "issues welcome, no PRs yet").
- License per M39 decision.
- Set up GitHub Actions CI (tests on push).

**Status:** Pending

---

### M46: Alpha Users

**Goal:** Get cadtool into the hands of 5–10 real users and collect structured feedback before a public launch.

**Scope:**
- Identify alpha testers: AI engineers, CAD hobbyists, agent-tool builders.
- Provide install instructions (PyPI or private repo access depending on M39).
- Give each tester a concrete task (e.g., "design a spur gear" or "model an enclosure").
- Collect friction logs: what confused them, what broke, what they wished existed.
- Prioritize feedback into v0.3 or v0.4 milestones.

**Status:** Pending

---

### M47: Launch — Product Hunt, Hacker News, Community Posts

**Goal:** Public announcement to drive awareness and early adoption.

**Scope:**
- Product Hunt launch: listing, tagline, screenshots/GIF, maker comment.
- Hacker News Show HN post.
- Reddit: r/cad, r/3Dprinting, r/MachineLearning, r/LocalLLaMA.
- Twitter/X thread with demo video.
- Relevant Discord/Slack communities (CadQuery, AI agent builders).
- Blog post or homepage write-up explaining the "CAD tool for agents" angle.

**Status:** Pending
