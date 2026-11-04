# Product requirements: Agent CAD workflow and editing reliability

**Status:** Reliability foundation delivered. The current benchmark evidence,
release decision, and stage-aligned roadmap are maintained in
[AgentCAD performance: release decision and next steps](agentcad-performance-next-steps.md).

**Evidence base:** September 12, 2026 GPT-5 Nano CADGenBench run, 81 fixtures

**Primary objective:** Increase meaningful task completion by making AgentCAD's
workflow, authoring surface, and recovery contracts predictable enough that an
agent can inspect evidence, build or edit geometry, validate it, and deliver the
correct artifact without repeated API guessing.

## Summary

AgentCAD should improve agent performance at two layers:

1. A required problem-solving workflow:
   **Inspect -> Specify -> Build -> Validate -> Deliver**.
2. A bounded, consistent product surface that accepts common high-confidence
   generated forms and returns copyable corrections for unsupported forms.

The workflow applies equally to new geometry and edits. For generation,
inspection means understanding drawings, dimensions, orientation, and
uncertainty. For editing, it additionally means understanding the source CAD,
identifying the precise target, and stating what must remain unchanged.

This is not a proposal for the benchmark harness to repair AgentCAD calls. The
headline benchmark uses the strict CLI tool contract and forwards string tokens
verbatim. Product help, errors, documentation, and compatibility behavior are
part of AgentCAD and therefore part of what the benchmark measures.

## Problem

The baseline run showed that many failures were not failures of spatial
reasoning. Agents often produced plausible geometry plans or scripts, then lost
the trial to predictable interface friction:

- guessed transform, primitive, placement, or export APIs;
- mixed wrapped build123d objects and raw OCCT shapes;
- omitted or incorrectly expressed output capture;
- imported helpers that existed only as injected globals;
- guessed bounding-box and vector property names;
- selected the wrong runtime despite an initialized project; or
- received an error that did not provide a safe next command.

These failures compound. One type mismatch or unclear correction can consume
several model/tool turns, leaving less time for geometric iteration and final
validation.

## Product principles

1. **Make the correct path obvious.** The installed skill, README, CLI help,
   and `agentcad docs` use the same five workflow stages and terminology.
2. **Normalize only high-confidence forms.** Accept unambiguous aliases and
   representations; reject ambiguous foreign APIs with a canonical correction.
3. **Preserve abstraction level.** A wrapped build123d input stays wrapped
   through normal editing helpers. Raw OCCT is an explicit escape hatch.
4. **Fail early and structurally.** Detect usage, capture, runtime, and path
   errors before daemon or CAD startup when possible.
5. **Make recovery executable.** A suggested command or code repair must be
   copyable, preserve relevant caller intent, and have a regression that proves
   it reaches the intended next stage.
6. **Validate intent and artifact health separately.** A structurally valid
   STEP is necessary but does not prove that the requested geometry is correct.
7. **Keep evaluation honest.** Strict, compatibility-assisted, and Python-direct
   runs are separate experiment contracts and are never aggregated together.

## Required workflow

### 1. Inspect

The agent examines every supplied requirement and input before authoring.

- Generation: drawings, views, units, explicit dimensions, repeated features,
  orientation, and material uncertainty.
- Editing: source STEP/BREP summary, measurements, render evidence, edit risk,
  relevant topology, and current structural findings.

Exit criterion: the agent can state the relevant geometry, coordinate frame,
units, and uncertainties that affect the result.

### 2. Specify

The agent writes a concrete acceptance contract before building:

- required features, dimensions, counts, relationships, and body structure;
- explicit assumptions where the prompt is incomplete;
- required output formats;
- for edits, the precise target and expected delta; and
- for edits, the geometry and properties that must remain stable.

Each requirement must have a planned measurement, comparison, structural
check, or visual review. Supported measurable requirements belong in
`spec.json`; broader requirements can live in `requirements.md`.

Exit criterion: another reviewer could judge the artifact without relying on
the reasoning used to construct it.

### 3. Build

The agent creates the simplest geometry that can meet the contract. Editing
uses the smallest bounded operation that changes the target without modifying
unrelated geometry. A dry run checks the script and metrics before a versioned
build when practical.

Exit criterion: AgentCAD has produced a candidate artifact and the agent knows
the exact returned output paths.

### 4. Validate

The agent proves both intent and deliverability:

- visual review of the preview and relevant orientations;
- required dimensions and feature counts;
- explicit `check-spec` results when applicable;
- for edits, visual and numeric evidence that the intended region changed and
  unrelated geometry remained stable;
- top-level STEP validity and round-trip findings; and
- independent validation of each requested mesh export.

If a check fails, the agent returns to the earliest stage whose assumptions or
implementation were wrong.

Exit criterion: every acceptance item has evidence and every deliverable has
the required structural verdict.

### 5. Deliver

The agent hands off only the exact artifacts that passed validation, using the
paths returned by AgentCAD. Labels are version names, not artifact paths.

Exit criterion: the response identifies the final STEP, requested exports, and
any material assumption or remaining limitation.

## Delivered P0 foundation

The dominant baseline blockers are closed:

| Issue | Outcome |
|---|---|
| [#189](https://github.com/jdilla1277/agentcad/issues/189) | Common tuple/vector/scalar transform calls |
| [#190](https://github.com/jdilla1277/agentcad/issues/190) | Early, actionable missing-output capture |
| [#191](https://github.com/jdilla1277/agentcad/issues/191) | One documented STEP output contract |
| [#192](https://github.com/jdilla1277/agentcad/issues/192) | Common generated primitive constructor aliases |
| [#193](https://github.com/jdilla1277/agentcad/issues/193) | Strict tool contract, diagnostic compatibility mode, and copyable CLI recovery |

The benchmark harness now records tool-contract identity and assistance counts.
`agentcad-cli` is the strict headline provider; `agentcad-cli-compat` is a
diagnostic provider only; `build123d-python` is the direct control.

## Remaining requirements

### P1: reliability and interoperability

| Issue | Requirement | Dependencies and coordination |
|---|---|---|
| [#194](https://github.com/jdilla1277/agentcad/issues/194) | Preserve build123d wrapper types through load, transform, Boolean, capture, and assembly paths | Foundation for #196, #197, and #203; land first or coordinate their type contracts |
| [#195](https://github.com/jdilla1277/agentcad/issues/195) | Expose the supported injected helper surface through one stable importable module | Coordinate exported names with #199; avoid importing CLI-heavy modules |
| [#196](https://github.com/jdilla1277/agentcad/issues/196) | Provide one ergonomic bbox/vector coordinate contract with targeted corrections | Build on #194's wrapped/raw contract; coordinate helper names with #195 |
| [#197](https://github.com/jdilla1277/agentcad/issues/197) | Normalize or diagnose common alignment, center, axis, and placement forms | Coordinate constructor behavior with #192 and transform behavior with #203 |
| [#198](https://github.com/jdilla1277/agentcad/issues/198) | Treat configured project runtime as authoritative before text-based ambiguity detection | Independent of geometry helper work; preserve explicit command override precedence |
| [#203](https://github.com/jdilla1277/agentcad/issues/203) | Add `dx/dy/dz` transform ergonomics and diagnose foreign transform forms | Follow-up to #189; align return types with #194 and public names with #195 |
| [#206](https://github.com/jdilla1277/agentcad/issues/206) | Distinguish function references from statically evident execution in capture analysis | Independent static-analysis slice; preserve #190's early-failure and runtime fallback behavior |

### P2: lower-frequency robustness

| Issue | Requirement | Dependencies and coordination |
|---|---|---|
| [#199](https://github.com/jdilla1277/agentcad/issues/199) | Support or diagnose common adjacent-library names such as `Vec` and `Translate` | Prefer diagnostics for ambiguous concepts; align import paths with #195 |
| [#200](https://github.com/jdilla1277/agentcad/issues/200) | Prevent internal `TreeError` when the same shape node is captured repeatedly | Independent capture/assembly work; define duplicate identity semantics explicitly |
| [#201](https://github.com/jdilla1277/agentcad/issues/201) | Finish self-correcting run errors for export-format, wrong-script-type, and remaining path mistakes | Re-audit after #193/PR #209; close satisfied criteria and retain only demonstrated gaps |

## Recommended parallel delivery plan

Use one Conductor workspace and one pull request per issue. Start each workspace
from the latest `main`; do not share a dirty worktree between agents.

### Wave 1: independent foundations

- **Workspace A — #194:** wrapped/raw geometry type contract.
- **Workspace B — #198:** configured runtime precedence.
- **Workspace C — #206:** capture reachability analysis.

These slices should have little implementation overlap. #194 is the critical
path for several authoring ergonomics issues.

### Wave 2: authoring surface

Begin after #194's contract is merged or explicitly shared with the agents:

- **#195:** stable importable API.
- **#196:** bbox/vector access.
- **#203:** remaining transform ergonomics.
- **#197:** placement and alignment.

Likely collision areas are helper exports, injected globals, guide text, and
shared tests. Assign one agent ownership of the public helper namespace and
require the other slices to consume that decision rather than invent aliases.

### Wave 3: robustness tail and benchmark rerun

- Re-audit and finish #199, #200, and #201.
- Run a small strict-contract pilot to catch harness or packaging failures.
- Run the complete GPT-5 Nano fixture set with the frozen experiment contract.
- Compare the result with the baseline and with the Python-direct control.

## Slice definition of done

Every issue must satisfy all of the following:

1. The issue's acceptance criteria are met without broadening ambiguous
   semantics.
2. Historical failing snippets become regression tests or replay fixtures.
3. Accepted forms preserve native supported behavior and object types.
4. Rejected forms return a targeted canonical correction before expensive work
   when feasible.
5. A literal correction is executed in at least one test when the response
   promises a copyable action.
6. Default build123d tests pass without the CadQuery extra; compatibility tests
   pass where the change touches CadQuery behavior.
7. The installed skill, CLI docs, and public API reference are updated when the
   agent-facing contract changes.
8. The pull request reports the before/after failure family and the exact
   verification commands.

## Evaluation plan

### Experiment contract

- Same fixture set, prompts, model, thinking level, timeouts, concurrency, and
  fixed post-trial evaluator across candidates.
- Headline AgentCAD runs use strict `agentcad-cli`: `{command, args}` only,
  string tokens forwarded verbatim, with no harness-authored product repair.
- Compatibility normalization runs use `agentcad-cli-compat` and are reported
  separately as interface-friction diagnostics.
- Python control runs use `build123d-python`.
- Never aggregate results whose provider, mode, or contract version differs.

### Primary metric

**Meaningful completion rate:** a submitted, post-trial-valid output that is new
or changed relative to every supplied input STEP. An unchanged input does not
count as meaningful completion.

### Leading indicators

- candidate failures grouped by stable reason;
- tool calls and retries before the first successful build;
- failures grouped by API family from the original benchmark taxonomy;
- strict-mode rejected calls and compatibility-mode normalized calls;
- no-submission and invalid-output rates;
- model cost and elapsed time per eligible trial; and
- for edits, evidence that the requested delta occurred without unrelated
  geometry changes.

Structural validity and file change are pipeline signals, not CAD-accuracy
scores. Accuracy still requires fixture-specific geometric evaluation.

## Non-goals

- Building a new CAD DSL.
- Emulating every CadQuery, build123d, OpenSCAD, or Blender spelling.
- Silently guessing among ambiguous shapes, placements, or outputs.
- Letting the benchmark harness teach AgentCAD product syntax in headline runs.
- Treating a plausible render or valid STEP as proof of task correctness.
- Solving feature recognition or direct-model editing comprehensively in this
  initiative.

## Risks and mitigations

- **Compatibility aliases change semantics.** Limit normalization to forms with
  one defensible interpretation; otherwise return a correction.
- **Parallel agents create competing public APIs.** Land #194 first and assign
  explicit ownership of helper exports before Wave 2.
- **Documentation drifts across surfaces.** Generate the installed skill from
  the same guide source and test README/help stage ordering.
- **Harness assistance inflates results.** Record contract identity and
  assistance counts; keep strict and compatibility experiments separate.
- **A fix moves failures downstream.** Replay historical snippets and inspect
  the next failure family rather than counting only the first removed error.
- **Valid output is unchanged or incorrect.** Track meaningful completion and
  preserve fixture-specific validation rather than relying on status alone.

## Decisions already made

- The product workflow is **Inspect -> Specify -> Build -> Validate -> Deliver**.
- Verify and structural validation are one evidence-gathering stage: Validate.
- The workflow covers both generation and editing.
- AgentCAD owns discoverability and recovery; the strict benchmark bridge does
  not normalize product arguments.
- Compatibility normalization remains available only as a separately labeled
  diagnostic experiment.

## Completion criteria for this initiative

The initiative is complete when:

1. All P1 issues above are closed with their acceptance criteria and replay
   coverage.
2. P2 issues are closed, explicitly deferred with evidence, or split into
   bounded follow-ups.
3. The five-stage workflow is consistent across the installed skill, README,
   CLI help, and built-in docs.
4. A full strict-contract GPT-5 Nano run is complete and comparable to the
   baseline and Python-direct control.
5. Results show whether meaningful completion improved, which failure families
   remain, and whether gains came from the product rather than harness repair.
