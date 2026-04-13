# agentcad — Roadmap (formerly cadtool)

Single source of truth for all milestones. For per-milestone detailed plans, see `prd/m{N}_plan.md`.

---

## Summary

| # | Milestone | Size | Status |
|---|-----------|------|--------|
| | **v0.1 — Core Pipeline** | | |
| M1 | Project scaffolding & `agentcad init` | S | Done |
| M2 | 2D geometry primitives | S | Done |
| M3 | Geometry enrichment | S | Done |
| M4 | `agentcad run` — script execution & STEP export | L | Done |
| M5 | Error handling & failed versions | S | Done |
| M6 | PNG rendering | M | Done |
| M7 | GLB & STL export | M | Done |
| M8 | `agentcad render` (custom views, `--zoom`) | M | Done |
| ~~M9~~ | ~~2D primitive cleanup~~ (absorbed into M4) | — | Done |
| M9b | `--focus` for `agentcad render` | S | Done |
| M10 | `agentcad context`, `agentcad docs` & `agentcad diff` | M | Done |
| M11 | Geometry helpers (`loft_sections`, `tapered_sweep`, `naca_wire`, `mirror_fuse`) | M | Done |
| M12 | `agentcad export` command | S | Done |
| M13 | OBJ export & end-to-end verification | S | Done |
| | | | |
| | **v0.2 — Fast Loop & Friction Fixes** | | |
| M14 | Geometric metrics in build output | S | Done |
| M15 | Script preamble — implicit runtime context | S | Done |
| M16 | Pre-execution validation | S | Done |
| M17 | Friction fixes — auto-compound, docs improvements | S | Done |
| M18 | Quick preview mode (`--preview`) | S | Done |
| M19 | Multi-solid colored GLB export | S | Done |
| M20 | Patterns docs, positioning helpers & `agentcad view` | M | Done |
| M21 | Parametric scripts (`--params`) | M | Done |
| M22 | Friction fixes — Python version check, rotate docs, dry-run | S | Done |
| M23 | Persistent worker / daemon mode | M | Done |
| M24 | Friction fixes — `agentcad inspect`, render quality, bug fixes | M | Done |
| M25 | `--help` operational briefing & README | S | Done |
| M26 | Performance warnings in docs | XS | Done |
| M27 | Friendlier `agentcad init` when project exists | XS | Done |
| M28 | Better Python version / import error diagnostics | S | Done |
| M29 | Progress indicator for long `agentcad run` | M | Done |
| M30 | `involute_gear_profile` helper | M | Done |
| M31 | User-specified part colors in GLB export | S | Done |
| M32 | File paths in `agentcad context` | XS | Done |
| M33 | Per-part design workflow (named parts, per-part metrics/renders) | L | Done |
| M34 | Assembly positioning helpers (`bbox_point`, `place_at`, `assemble`) | S | Done |
| M35 | Wire helpers & `elliptical_sweep` | S | Done |
| M36 | Validity warnings & diagnostics | S | Done |
| M37 | Wire helper robustness (point dedup) | S | Done |
| M38 | Complex profile patterns in docs | S | Done |
| | | | |
| | **v0.3 — Distribution & Agent Ecosystem** | | |
| M39 | Openness & licensing strategy | S | Done |
| M40 | Rename to agentcad | M | Done |
| M41 | Publish to PyPI | S | Done |
| M42 | MCP server | M | Done |
| M43 | `agentcad skill install` (replaces M43/M44) | S | Done |
| M44 | Homepage | M | Pending |
| M45 | Session logging & feedback | S | Done |
| M46 | Remote feedback (`agentcad feedback` → you) | S | Pending |
| M47 | Alpha users (10–100 testers, collect feedback) | M | Pending |
| M48 | Skill marketplace publishing (ClawHub, Claude) | S | Pending |
| M49 | Launch — Product Hunt, Hacker News, community posts | S | Pending |
| | | | |
| | **v0.4 — Multi-Part Assembly & Tooling** | | |
| M50 | Inter-part constraints (mate, align, coaxial) | L | Pending |
| M51 | Part instancing & patterns (linear, circular array) | M | Pending |
| M52 | Tolerance / fit helpers (clearance, interference) | S | Pending |
| M53 | Assembly validation (interference detection) | M | Pending |

---

## Where we are

**v0.1 (Core Pipeline):** Done. agentcad can execute CadQuery scripts, render PNGs, export to STEP/GLB/STL/OBJ, diff versions, and show docs. 368 tests, 10 commands.

**v0.2 (Fast Loop & Friction Fixes):** Done. 38 milestones shipped: metrics, preamble, validation, daemon, parametric scripts, inspect, per-part workflow, assembly helpers, wire helpers, validity warnings, wire dedup, complex profile docs.

**v0.3 (Distribution & Agent Ecosystem):** In progress. Licensing (M39), rename (M40), MCP server (M42), skill install (M43), session logging (M45) done. Next: PyPI publish (M41).

**v0.4 (Multi-Part Assembly & Tooling):** Not started. Inter-part constraints, part instancing, tolerance helpers, interference detection.

---

## v0.1 — Core Pipeline

### M1: Project Scaffolding & `agentcad init` ✓

**Goal:** A pip-installable CLI that runs `agentcad init` and produces correct project structure.

**Delivered:** 12 tests, `agentcad init` command with JSON output, Click CLI, pytest suite.

---

### M2: 2D Geometry Primitives ✓

**Goal:** Let agents create and inspect geometry via `add-rect`, `add-circle`, and `list`.

**Delivered:** 38 tests, 4 commands (`init`, `add-rect`, `add-circle`, `list`), shared manifest module, auto-incrementing IDs.

---

### M3: Geometry Enrichment ✓

**Goal:** Labels, count, delete, and get — close the CRUD gaps before moving to 3D.

**Delivered:** 58 tests, 6 commands, optional `--label` on shapes, `count` in list response.

---

### M4: `agentcad run` — Script Execution & STEP Export ✓

**Goal:** Execute a CadQuery script and produce a versioned STEP file.

**Delivered:** 25 tests, 2 commands (`init`, `run`). CadQuery 2.7.0 via CQGI, STEP export, versioned directories, meta.json, manifest tracking. 2D primitives removed (M9 absorbed).

---

### M5: Error Handling & Failed Versions ✓

**Goal:** Failed runs are preserved with `_failed` suffix, error details in JSON.

**Delivered:** 30 tests. Script failures create `_failed` directories with `script.py` + `meta.json`, tracked in manifest with `status: "failed"`. Failed runs consume version numbers; `current` does not advance.

---

### M6: PNG Rendering ✓

**Goal:** Produce PNG renders from CadQuery results for agent visual inspection.

**Delivered:** 43 tests. OCP offscreen rendering via `V3d_View.ToPixMap`. `--render` option on `agentcad run` accepts comma-separated views. 7 views available; `all` expands to front, right, top, iso.

---

### M7: GLB & STL Export ✓

**Goal:** Mesh exports for web viewers, 3D printing, and agent interop.

**Delivered:** 53 tests. `--export stl,glb` option on `agentcad run`. STL via CadQuery exporters. GLB via OCP `RWGltf_CafWriter`.

---

### M8: `agentcad render` ✓

**Goal:** Render additional views of an existing STEP file without creating a new version.

**Delivered:** 74 tests, 3 commands. `agentcad render <step_path> --view <spec>` with named views, custom angles, `--zoom`, `--name`.

---

### ~~M9: 2D Primitive Cleanup~~ ✓ (absorbed into M4)

---

### M9b: `--focus` for `agentcad render` ✓

**Goal:** Make `agentcad render` zoom usable for detail inspection.

**Delivered:** 84 tests. `--focus x,y,z` sets camera target. `--no-fit` skips `FitAll()`. `_apply_camera()` helper extracted.

---

### M10: `agentcad context`, `agentcad docs` & `agentcad diff` ✓

**Goal:** Agent discoverability and version comparison commands.

**Delivered:** 111 tests, 6 commands. `agentcad context` returns project state. `agentcad docs [section]` returns hardcoded documentation. `agentcad diff <ref1> <ref2>` compares versions.

---

### M11: Geometry Helpers ✓

**Goal:** Reusable organic geometry primitives for CadQuery scripts.

**Delivered:** `agentcad.helpers` module with `loft_sections`, `tapered_sweep`, `naca_wire`, `mirror_fuse`.

---

### M12: `agentcad export` Command ✓

**Goal:** Post-hoc mesh export from existing STEP files without re-running scripts.

**Delivered:** 141 tests, 7 commands. `agentcad export <step_file> --format stl,glb`.

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

**Goal:** Every successful `agentcad run` returns geometric metrics (bbox, volume, area, face/edge counts, validity) so agents can verify shape correctness without rendering.

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

**Goal:** `--preview` flag on `agentcad run` produces a fast 256x256 iso PNG for shape verification during iteration.

**Delivered:** 210 tests. Preview path in output JSON and meta.json. Coexists with `--render`.

---

### M19: Multi-Solid Colored GLB Export ✓

**Goal:** GLB exports preserve individual solids with per-part colors.

**Delivered:** 215 tests. `export_glb()` decomposes compounds, assigns colors from 10-color palette via `XCAFDoc_ColorTool`.

---

### M20: Patterns Docs, Positioning Helpers & `agentcad view` ✓

**Goal:** Eliminate positioning footguns, make GLB viewable, allow mixed view specs.

**Delivered:** 234 tests, 8 commands. `agentcad docs patterns`, `translate()` + `rotate()` helpers, `agentcad view <file>`, mixed view specs (`front,right,45:15`).

---

### M21: Parametric Scripts ✓

**Goal:** `--params key=val,key=val` on `agentcad run` passes parameter overrides to CQGI.

**Delivered:** 247 tests. Type coercion, unknown param detection, params in output JSON, `agentcad diff` shows param changes.

---

### M22: Friction Fixes — Desk Lamp ✓

**Goal:** Fix P0/P1 issues from desk lamp friction test.

**Delivered:** 254 tests. Python version check, `rotate()` direction docs, `--dry-run` mode, angled positioning example.

---

### M23: Persistent Worker (Daemon Mode) ✓

**Goal:** Background process keeps CadQuery/OCP loaded in memory, eliminating 3-5s cold start.

**Delivered:** ~282 tests, 9 commands. `agentcad daemon start/stop/status`. Unix domain socket IPC, eager module warm-up, auto-routing from `agentcad run`.

---

### M24: Friction Fixes — Helical Gear ✓

**Goal:** Fix geometry debugging gaps from helical gear friction test.

**Delivered:** ~304 tests, 10 commands. `agentcad inspect`, validity diagnostics, negative volume warning, brighter renders, relative path support, version dir collision fix, custom angles in `--render`.

---

### M25: `--help` Operational Briefing & README

**Goal:** Make `agentcad --help` a complete operational briefing so an agent can be productive on first contact.

**Status:** In Progress (PR #15)

---

### M26: Performance Warnings in Docs

**Goal:** Document the twistExtrude + spline performance cliff so agents don't burn timeouts.

**Scope:** Add warning to `agentcad docs patterns`. 1 test, 1 docs string edit.

**Status:** Pending

---

### M27: Friendlier `agentcad init` Error ✓

**Goal:** When project already initialized, tell the agent to run `agentcad context`.

---

### M28: Better Python Version / Import Error Diagnostics ✓

**Goal:** When CadQuery/OCP imports fail due to wrong Python version, tell the agent why.

---

### M29: Progress Indicator for Long Runs ✓

**Goal:** Periodic progress heartbeats to stderr during long `agentcad run` executions.

---

### M30: `involute_gear_profile` Helper ✓

**Goal:** `involute_gear_profile(module, teeth, pressure_angle)` returns a closed TopoDS_Wire of an involute spur gear profile.

**Delivered:** 8 new tests. Full involute curve generation with root/tip arcs. Added to preamble and docs.

---

### M31: User-Specified Part Colors in GLB Export ✓

**Goal:** Let scripts assign colors to parts via `show_object()` options. Falls back to auto-palette.

---

### M32: File Paths in `agentcad context` ✓

**Goal:** Each version in `agentcad context` output includes its file paths.

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

### M36: Validity Warnings & Diagnostics ✓

**Goal:** Make geometry failures loud and actionable instead of buried in metrics JSON.

**Delivered:** 387 tests. `is_valid: false` and metrics warnings (negative volume) promoted to top-level `warnings` list in output JSON and meta.json. `warning` (singular string) → `warnings` (list). Negative volume warning includes wire winding guidance. `BRep_API: command not done` enriched with wire closure context.

---

### M37: Wire Helper Robustness (Point Dedup) ✓

**Goal:** Prevent `Knots interval values too close` and `BSplCLib::Interpolate` errors in wire helpers.

**Delivered:** 391 tests. `_dedup_points(points, tol=1e-6)` shared helper. `spline_wire` and `polygon_wire` both deduplicate before passing to OCC. ValueError if dedup reduces below 3 points.

---

### M38: Complex Profile Patterns in Docs ✓

**Goal:** Document construction strategies that avoid the most common BRep footguns for complex profiles.

**Delivered:** 394 tests. Three new sections in `agentcad docs patterns`: subtractive construction (cut from blank), wire winding direction (CCW/CW + normals), mixed edge type wires (`BRepBuilderAPI_MakeWire`).

---

## v0.3 — Distribution & Agent Ecosystem

**Goal:** Make agentcad installable by anyone and discoverable by every major AI agent platform.

**Reference implementations:** [Stripe Projects](https://projects.dev/) (CLI-first, agent-friendly provisioning), [RAMP CLI](https://github.com/ramp-public/ramp-cli) (`--agent` flag, `ramp skills` command, separate MCP server).

**Suggested order:** M41 (PyPI) → M42 (MCP) → M44 (Claude skill) → M43 (ClawHub) → M45 (homepage) → M46 (public repo)

---

### M39: Openness & Licensing Strategy

**Goal:** Decide how open agentcad will be — public vs. private repo, open-source vs. source-available vs. proprietary, license choice — and document the rationale.

**Scope:**
- Decide: public GitHub repo or private with pip-installable distribution only?
- Choose a license (MIT, Apache 2.0, BSL, proprietary, etc.) or explicitly defer.
- Consider implications for each distribution channel (PyPI, MCP, ClawHub, skills).
- Document the decision and reasoning in this roadmap or a dedicated doc.
- Gates M46 (public repo) — can't open the repo without deciding what "open" means.

**Decision:** BSL 1.1 — public repo, source-available, free for all use except offering agentcad as a competing hosted service. Converts to Apache 2.0 after 4 years per release. Full rationale in `prd/m39_licensing_strategy.md`.

**Status:** Done

---

### M40: Rename to agentcad ✓

**Goal:** Rename the package, CLI, and all references from `cadtool` to `agentcad` before first public release.

**Delivered:** Package directory renamed, all imports/env vars/socket paths/manifest filename/CLI entry point/docs updated. 394 tests passing. `agentcad` CLI works.

---

### M41: Publish to PyPI

**Goal:** `pip install agentcad` (or `pipx install agentcad`) works for anyone.

**Scope:**
- Register "agentcad" on PyPI (or alternative name if taken).
- Add metadata to `pyproject.toml`: description, authors, readme, urls, classifiers.
- Test publish on TestPyPI first.
- Set up GitHub Actions for automated publishing via PyPI Trusted Publishers (OIDC, no API tokens).
- Version bump to 0.2.0 for first public release.

**Status:** Pending

---

### M42: MCP Server

**Goal:** A Model Context Protocol server that exposes agentcad commands as native agent tools. Works with Claude Code, Cursor, Windsurf, VS Code Copilot, and any MCP-compatible agent.

**Scope:**
- New module `agentcad/mcp_server.py` using the `mcp` Python SDK (`FastMCP`).
- Expose tools: `run`, `render`, `export`, `inspect`, `docs`, `context`, `diff`, `view`.
- stdio transport (client spawns the process).
- Users configure via `.mcp.json`: `{"agentcad": {"command": "python", "args": ["-m", "agentcad.mcp"]}}`.
- Structured JSON responses (agentcad already returns these — thin wrapper).
- Add `mcp` to optional dependencies in `pyproject.toml`.
- Tests for tool registration and response format.

**Status:** Pending

---

### M43: `agentcad skill install` ✓

**Goal:** Self-installing agent skill bundled with the package.

**Delivered:** 424 tests. `agentcad skill install` writes SKILL.md to `.claude/skills/agentcad/` for Claude Code auto-discovery. `agentcad skill show` prints content as JSON. Skill covers full workflow, script rules, debugging playbook, patterns. Follows Agent Skills spec. Replaces separate ClawHub (old M43) and Claude Code skill (old M44) milestones.

---

### M44: Homepage

**Goal:** A public website (e.g., agentcad.dev) that explains what agentcad is, shows examples, and links to install instructions for every agent platform.

**Scope:**
- Landing page: hero, value prop, install snippet, demo GIF/video.
- Per-platform setup guides: Claude Code, Cursor, Windsurf, OpenClaw, Codex.
- Link to PyPI.
- Static site (GitHub Pages, Vercel, or Cloudflare Pages).

**Status:** Pending

---

### M45: Session Logging & Feedback ✓

**Goal:** Capture agent interaction patterns and friction signals for analysis.

**Delivered:** `SessionLogger` auto-logs every command to `.agentcad/session.jsonl`. `agentcad feedback` bundles message with session history, friction signals, and environment info. `AGENTCAD_NO_LOG` env var to disable. Defensive logging — failures never break the CLI.

---

### M46: Remote Feedback

**Goal:** `agentcad feedback` sends friction bundles directly to you, no manual file sharing. Designed for 10–100 testers.

**Scope:**
- Agent runs `agentcad feedback "message"` → bundle is POSTed to a remote endpoint (webhook, API, or GitHub issue on a public feedback repo).
- Tester doesn't need repo access, GitHub account, or any manual steps.
- Bundle includes session log, friction signals, environment info (already built in M45).
- Design the receiving side: where bundles land, how you triage them.

**Status:** Pending

---

### M47: Alpha Users

**Goal:** Get agentcad into the hands of 10–100 real users and collect structured feedback.

**Scope:**
- Identify alpha testers: AI engineers, CAD hobbyists, agent-tool builders.
- Provide install instructions via PyPI.
- Give each tester a concrete task (e.g., "design a phone stand" or "model an enclosure").
- Collect friction logs via `agentcad feedback` (M46).
- Prioritize feedback into v0.3 or v0.4 milestones.

**Status:** Pending

---

### M48: Skill Marketplace Publishing

**Goal:** Publish agentcad skill to ClawHub and Claude skill marketplace for broader discovery.

**Scope:**
- Publish to ClawHub via `clawhub publish`. Verify via `clawhub search "CAD"`.
- Publish to Claude skill marketplace (when available).
- Skill content is already built (M43) — this is distribution only.

**Status:** Pending

---

### M49: Launch — Product Hunt, Hacker News, Community Posts

**Goal:** Public announcement to drive awareness and early adoption.

**Scope:**
- Product Hunt launch: listing, tagline, screenshots/GIF, maker comment.
- Hacker News Show HN post.
- Reddit: r/cad, r/3Dprinting, r/MachineLearning, r/LocalLLaMA.
- Twitter/X thread with demo video.
- Relevant Discord/Slack communities (CadQuery, AI agent builders).
- Blog post or homepage write-up explaining the "CAD tool for agents" angle.

**Status:** Pending

---

## v0.4 — Multi-Part Assembly & Tooling

**Goal:** Make multi-part assemblies as smooth as single-part design. Driven by assembly friction in real agent workflows.

---

### M50: Inter-Part Constraints

**Goal:** Declarative constraints between parts (mate, align, coaxial) so agents don't manually compute coordinates.

**Scope:**
- `mate(part_a, face_a, part_b, face_b)` — align faces flush.
- `align(part, axis, target_axis)` — align part axis to target.
- `coaxial(part_a, part_b)` — align cylindrical axes.
- Returns positioned `TopoDS_Shape`, composable with existing helpers.

**Status:** Pending

---

### M51: Part Instancing & Patterns

**Goal:** Create arrays of identical parts without manual repetition.

**Scope:**
- `linear_pattern(shape, direction, count, spacing)` — linear array.
- `circular_pattern(shape, axis, count)` — rotational array.
- Returns compound of instances.

**Status:** Pending

---

### M52: Tolerance / Fit Helpers

**Goal:** Helpers for common mechanical fit calculations.

**Scope:**
- `clearance_fit(bore_d, shaft_d)` — verify clearance is positive.
- `press_fit(bore_d, shaft_d)` — verify interference is within range.
- Integrated with `compute_metrics` — report fit analysis when parts are named.

**Status:** Pending

---

### M53: Assembly Validation

**Goal:** Detect interference between parts before export.

**Scope:**
- `check_interference(compound)` — pairwise `BRepAlgoAPI_Common` between solids.
- Report overlapping pairs with volume of interference.
- Surface as warning in `agentcad run` output (like `is_valid` warnings).

**Status:** Pending
