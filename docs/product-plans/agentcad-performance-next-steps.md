# AgentCAD editing performance: effort closeout

**Period:** September–October 2026

**Status:** Experiment and reliability initiative complete; remaining work is
captured in the issue backlog

**Decision:** Release the accumulated product fixes together with the
five-stage agent workflow: **Inspect -> Specify -> Build -> Validate ->
Deliver**

## Why we did this

The published GPT-5 Nano benchmark showed that AgentCAD improved generation
and validity, but editing was its weakness: `.249` with AgentCAD versus `.279`
with direct build123d. Transcript review showed that agents often had a sound
geometric plan and still failed because they guessed an API, lost output
capture, selected the wrong runtime, applied a global operation, or treated a
structurally valid STEP as proof that the requested edit was correct.

We pursued two linked hypotheses:

1. Remove recurring API and recovery friction so an agent can execute the plan
   it already has.
2. Teach a general workflow—Inspect, Specify, Build, Validate, Deliver—that
   applies to generation and editing and makes correctness evidence explicit.

## What we changed

### 1. Removed the execution blockers

This effort found and shipped 19 product reliability and interoperability
fixes. Most occurred in Build because that was where otherwise reasonable
agent plans were being lost.

| Stage | Shipped findings |
|---|---|
| **Inspect** | [#196](https://github.com/jdilla1277/agentcad/issues/196) added dependable bounding-box/vector coordinate access and recovery guidance. |
| **Specify** | No distinct implementation bug was isolated; the gap was that specification remained optional prose. |
| **Build** | [#189](https://github.com/jdilla1277/agentcad/issues/189) and [#203](https://github.com/jdilla1277/agentcad/issues/203) fixed transform ergonomics; [#192](https://github.com/jdilla1277/agentcad/issues/192) fixed common primitive forms; [#194](https://github.com/jdilla1277/agentcad/issues/194) preserved wrapper types; [#197](https://github.com/jdilla1277/agentcad/issues/197), [#217](https://github.com/jdilla1277/agentcad/issues/217), and [#218](https://github.com/jdilla1277/agentcad/issues/218) repaired placement/alignment guidance; [#195](https://github.com/jdilla1277/agentcad/issues/195) and [#199](https://github.com/jdilla1277/agentcad/issues/199) stabilized and diagnosed the authoring surface; [#198](https://github.com/jdilla1277/agentcad/issues/198) fixed runtime selection; [#190](https://github.com/jdilla1277/agentcad/issues/190), [#200](https://github.com/jdilla1277/agentcad/issues/200), and [#206](https://github.com/jdilla1277/agentcad/issues/206) fixed output capture; [#193](https://github.com/jdilla1277/agentcad/issues/193), [#201](https://github.com/jdilla1277/agentcad/issues/201), [PR #208](https://github.com/jdilla1277/agentcad/pull/208), and [PR #227](https://github.com/jdilla1277/agentcad/pull/227) made CLI/MCP calls recoverable and machine-readable. |
| **Validate** | Existing deliverable validation correctly caught the kernel, shell, and manifold defects encountered in the later Opus run. The remaining problem was carrying those verdicts into delivery readiness. |
| **Deliver** | [#191](https://github.com/jdilla1277/agentcad/issues/191) established one stable STEP output contract so agents deliver AgentCAD's tracked artifact rather than guessing an export API. |

All issue-backed items above are closed. PRs #208 and #227 were smaller defects
found and fixed directly during fresh-agent review; their merged PR descriptions
are the canonical reproductions and regression records.

### 2. Made the workflow explicit

We reorganized the injected skill, README, help, and built-in documentation
around a required loop:

- **Inspect:** understand inputs, orientation, units, uncertainty, and source
  geometry.
- **Specify:** state requirements, assumptions, edit target, invariants, and
  planned checks.
- **Build:** perform the simplest bounded operation that can satisfy the
  contract.
- **Validate:** separately prove artifact health, requested intent, and source
  preservation; loop back when evidence fails.
- **Deliver:** hand off the exact artifact for which the evidence was produced.

The benchmark harness remained strict: it exposed AgentCAD's real command
surface without repairing calls, supplying geometry facts, or leaking evaluator
feedback.

## Results

The benchmark published in
[Measuring AgentCAD's effectiveness](https://agentcad.dev/measuring-agentcad-effectiveness)
is the effective baseline: the runs use the same model family and reasoning
effort, the same 81 fixtures, and the same pinned CADGenBench dataset revision.

| Metric | Published AgentCAD | [Candidate K: current product](https://huggingai4engineering-cadgenbench.hf.space/reports/james-dillard_gpt-5-nano-candidate-k-pi-harness-contro_20261001-022034.html) | [Candidate L: five-stage workflow](https://huggingai4engineering-cadgenbench.hf.space/reports/james-dillard_gpt-5-nano-candidate-l-pi-harness-five-s_20261001-001100.html) |
|---|---:|---:|---:|
| Overall CAD Score | .184 | .217 | **.245** |
| Generation | .142 | .156 | **.175** |
| Editing | .249 | .310 | **.353** |
| Valid outputs | 67/81 | 78/81 | **80/81** |
| Validity | 82.7% | 96.3% | **98.8%** |
| Model cost | $0.231 | **$0.191** | $0.211 |

Candidate K improved on the published AgentCAD result by 17.9% overall, 9.9%
on generation, and 24.5% on editing while reducing model cost by 17.4%.
Candidate L improved further: versus the published result it was 33.2% higher
overall, 23.2% higher on generation, and 41.8% higher on editing, with 13
additional valid outputs.

The original editing weakness reversed. Candidate K reached `.310` and
Candidate L reached `.353`, both above the original direct-build123d result of
`.279`.

Stronger-model runs showed that model quality remains the largest driver while
the workflow provides a smaller, directionally repeated lift:

| Candidate | Configuration | Overall | Generation | Editing | Validity |
|---|---|---:|---:|---:|---:|
| K | Nano, standard AgentCAD | .217 | .156 | .310 | 96.3% |
| L | Nano, five-stage workflow | .245 | .175 | .353 | 98.8% |
| M | Luna, standard AgentCAD | .329 | .301 | .373 | 97.5% |
| N | Luna, five-stage workflow | .340 | .303 | .397 | 100% |
| P | Opus 5.5, standard AgentCAD | .447 | .352 | .593 | 97.5% |

## What we learned

- **The product is materially better.** Current AgentCAD improved quality,
  editing, validity, token use, and cost relative to the published result.
- **Editing is now a strength.** It improved the most and surpassed the
  historical direct-build123d score.
- **Model quality dominates.** Opus substantially outperformed Luna, which
  substantially outperformed Nano.
- **Workflow guidance helps, but is not enforcement.** It improved Nano and
  Luna overall, especially editing and validity, but Luna's fixture-level
  treatment was mixed: 33 wins, 37 losses, 11 ties, and a median delta of zero.
- **Text changed behavior unevenly.** K and L never used `measure` or
  `check-spec` and wrote no specifications. Luna adopted requirements and specs
  for generation, but barely for editing.
- **Agents can ignore correct validation evidence.** Luna fixture 118 submitted
  immediately after `check-spec` reported nine required holes versus 17 found.
  Opus fixtures 202 and 250 were rejected by AgentCAD's deliverable profile,
  but the agent switched to the reduced `kernel` profile and submitted anyway.
- **Validity is not correctness.** Several valid outputs were unchanged source
  geometry, global edits, or broad approximations. Artifact health, requested
  intent, and source preservation need separate verdicts.
- **Evaluation can create false conclusions.** STEP byte changes mislabeled
  re-exports as edits, disk exhaustion contaminated one condition, and a retry
  reran the model instead of validating the frozen artifact.

## Remaining work, handed off by workflow stage

Every unresolved **bug** below has a developer-ready GitHub issue with evidence,
scope, acceptance criteria, and regression expectations. **Features** are
understood directions that need product scoping. **Future ideas** require
validation before implementation.

| Stage | Bugs | Features | Future ideas |
|---|---|---|---|
| **Inspect** | None isolated. | Targeted semantic queries by surface, axis, diameter, location, thickness, and risk. | Persistent semantic feature identity across edits instead of unstable topology IDs. |
| **Specify** | None isolated. | Structured generation/editing contracts for requirements, assumptions, target delta, preservation constraints, and planned checks. | Compile drawings and natural language into executable geometry contracts with explicit uncertainty. |
| **Build** | [#230](https://github.com/jdilla1277/agentcad/issues/230): build-root recovery recommends initializing a guessed alternate root instead of using the current project. | [#231](https://github.com/jdilla1277/agentcad/issues/231): cheap script preflight distinct from full STEP validation; more bounded editing operations. | Adaptive model and strategy routing based on inspection uncertainty and repeated failure. |
| **Validate** | [#238](https://github.com/jdilla1277/agentcad/issues/238): failed `check-spec` evidence is command-local and can be ignored. | [#229](https://github.com/jdilla1277/agentcad/issues/229): automatic source/output comparison for direct `load_step` edits. [#232](https://github.com/jdilla1277/agentcad/issues/232): separate artifact, intent, and preservation verdicts. | Independent verification and bounded repair that can reject the builder's conclusion. |
| **Deliver** | [#237](https://github.com/jdilla1277/agentcad/issues/237): reduced-profile artifacts can look delivery-ready even when required layers were skipped. | A delivery receipt bound to artifact/spec hashes, validation profile, checks, preservation evidence, and overrides. | Verifiable CAD provenance that downstream agents and humans can consume as a trust contract. |

## Evaluation handoff

### Next evaluations

1. Run Luna with AgentCAD and directly with build123d to measure generic lift
   at a cost-effective model tier.
2. Run an eight-edit Opus 5.5 gate with direct build123d, standard AgentCAD,
   and five-stage AgentCAD; expand only if the small panel is decision-relevant.
3. Study high-signal workflow wins (`209`, `224`, `225`, `238`), regressions
   (`212`, `230`), source-preservation failure (`202`), and validity rescue
   (`250`).
4. Measure stage adoption directly: target found, contract present, bounded
   build, intent evidence, preservation evidence, failed-check repair, and
   exact checked artifact delivered.

### Evaluation bugs and infrastructure

- [Internal #346](https://github.com/jdilla1277/agentcad-internal/issues/346)
  — replace STEP-SHA edit classification with geometry-aware comparison.
- [Internal #347](https://github.com/jdilla1277/agentcad-internal/issues/347)
  — stop admitting trials after disk exhaustion.
- [Internal #348](https://github.com/jdilla1277/agentcad-internal/issues/348)
  — resume infrastructure failures without rerunning or silently replacing the
  candidate answer.
- [Internal #349](https://github.com/jdilla1277/agentcad-internal/issues/349)
  — allow an honest no-artifact outcome instead of encouraging placeholders.
- [Internal #350](https://github.com/jdilla1277/agentcad-internal/issues/350)
  — interleave matched conditions and separate model, CAD, and evaluator time.
- [Internal #351](https://github.com/jdilla1277/agentcad-internal/issues/351)
  — test observable stage evidence and delivery gates on a diagnostic subset.

## Closeout and release recommendation

This initiative achieved its immediate goals: the dominant execution failures
were converted into shipped fixes, the general workflow was defined and tested,
editing performance improved materially, and the remaining product and
evaluation work is captured in bounded issues.

Release the accumulated product improvements with the five-stage help. Position
the release around **editing reliability, deliverable geometry, and a structured
agent workflow**. The evidence supports claims of **33% higher overall CAD
quality**, **42% higher editing quality**, and **80 of 81 valid outputs** versus
the previously published AgentCAD result, at lower model cost.

Describe Inspect -> Specify -> Build -> Validate -> Deliver as the workflow
AgentCAD teaches today. The next product chapter is making its evidence,
failure handling, and exact-artifact delivery executable.
