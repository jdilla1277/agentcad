# AgentCAD editing performance: effort closeout

**Period:** September–October 2026

**Status:** Experiment and reliability initiative complete; remaining work is
captured in the issue backlog

**Decision:** Release the accumulated product fixes together with the
five-stage agent workflow: **Inspect -> Specify -> Build -> Validate ->
Deliver**. Publish Candidate L as the release result; Candidate K is the
intermediate product-only waypoint.

The reproducibility details, behavioral counts, per-fixture findings, costs,
limitations, and package hashes are preserved in the
[evidence appendix](./agentcad-performance-evidence.md).

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
validation before implementation. Evaluate is the outer product-development
loop that improves the evidence and feeds the other five stages; it is not a
sixth task step every CAD agent must perform.

| Stage | Bugs | Features | Future ideas |
|---|---|---|---|
| **Inspect** | None isolated. | Targeted semantic queries by surface, axis, diameter, location, thickness, and risk. | Persistent semantic feature identity across edits instead of unstable topology IDs. |
| **Specify** | None isolated. | Structured generation/editing contracts for requirements, assumptions, target delta, preservation constraints, and planned checks. | Compile drawings and natural language into executable geometry contracts with explicit uncertainty. |
| **Build** | [#230](https://github.com/jdilla1277/agentcad/issues/230): build-root recovery recommends initializing a guessed alternate root instead of using the current project. | [#231](https://github.com/jdilla1277/agentcad/issues/231): cheap script preflight distinct from full STEP validation; more bounded editing operations. | Adaptive model and strategy routing based on inspection uncertainty and repeated failure. |
| **Validate** | [#238](https://github.com/jdilla1277/agentcad/issues/238): failed `check-spec` evidence is command-local and can be ignored. | [#229](https://github.com/jdilla1277/agentcad/issues/229): automatic source/output comparison for direct `load_step` edits. [#232](https://github.com/jdilla1277/agentcad/issues/232): separate artifact, intent, and preservation verdicts. | Independent verification and bounded repair that can reject the builder's conclusion. |
| **Deliver** | [#237](https://github.com/jdilla1277/agentcad/issues/237): reduced-profile artifacts can look delivery-ready even when required layers were skipped. | A delivery receipt bound to artifact/spec hashes, validation profile, checks, preservation evidence, and overrides. | Verifiable CAD provenance that downstream agents and humans can consume as a trust contract. |
| **Evaluate** | [Internal #346](https://github.com/jdilla1277/agentcad-internal/issues/346): geometry-aware edit classification. [#347](https://github.com/jdilla1277/agentcad-internal/issues/347): disk admission. [#348](https://github.com/jdilla1277/agentcad-internal/issues/348): canonical retries. [#349](https://github.com/jdilla1277/agentcad-internal/issues/349): honest no-artifact outcomes. | [Internal #350](https://github.com/jdilla1277/agentcad-internal/issues/350): interleaved conditions and phase timing. [#351](https://github.com/jdilla1277/agentcad-internal/issues/351): observable stage evidence and gates. A versioned run manifest and automatic evidence bundle. | A continuous regression system that maps score changes to workflow stages, behavioral causes, model/cost tiers, and release gates. |

## Evaluation handoff

### 1. Publish Candidate L

Candidate L—not K—is the release configuration and the next result to publish.
K is still important because it shows the accumulated product improvements
before staged help; L measures the experience users will receive. Publish the
K-to-L decomposition alongside L so the workflow is not credited for fixes
that were already present in K. Treat the result as a directional one-run
comparison, and report generation, editing, validity, cost, and latency rather
than only the aggregate score.

### 2. Run a publishable Opus 5.5 head-to-head

Candidate P proves that Opus 5.5 performs strongly with AgentCAD, but it cannot
measure AgentCAD's lift: it used standard AgentCAD help and has no matched
direct-build123d control. The external xhigh result is also not a control
because its reasoning effort and harness differ.

For a marketing-quality comparison, freeze two primary arms:

- **Control:** Claude Opus 5.5 standard effort, Pi, Python/build123d, and the
  filesystem, with no AgentCAD or workflow help.
- **Treatment:** the same model, effort, prompts, and Pi harness, with the
  release-candidate AgentCAD CLI and Candidate L's five-stage help.

An optional third diagnostic arm—standard AgentCAD help as used by P—would
separate product lift from workflow lift, but is not required for the headline
direct-versus-AgentCAD claim.

Use the same pinned 81-fixture dataset, timeouts, concurrency, evaluator, and
submission rules in every arm. Give the control the real tools an unaided agent
would have, and the treatment AgentCAD's real public surface; neither arm gets
harness-only geometry facts, call repair, or evaluator feedback. Interleave
conditions fixture-by-fixture with a seeded balanced order, freeze each answer
before evaluation, and retry infrastructure/evaluation without rerunning the
model.

First run the eight high-signal editing fixtures below as an infrastructure and
method gate. If there is no systematic failure, run all 81 fixtures in both
primary arms. Repeat at least the high-signal and discordant fixtures to
estimate sampling noise. Predeclare overall CAD Score as the primary endpoint,
with generation, editing, validity, cost, tokens, and wall time as secondary
outcomes. Publish the exact model/effort identifier, dataset, harness, wheel,
skill, and evaluator hashes plus per-fixture results. A defensible claim would
be: “In this matched CADGenBench run, AgentCAD plus its staged workflow changed
Opus 5.5 performance from X to Y,” not a universal model claim.

### 3. Explain where the workflow helps and hurts

Use fixtures `209`, `224`, `225`, and `238` as workflow wins; `212` and `230`
as regressions; `202` as the source-preservation hard case; and `250` as the
validity-rescue case. For each fixture and condition, retain the prompt,
transcript/tool trace, generated script and spec, source and result geometry,
previews/diffs, validator evidence, final answer, score, cost, tokens, and
timing. Then score every workflow stage `0` (absent), `1` (claimed), or `2`
(evidence-backed):

1. **Inspect:** Did the agent identify the correct source, target, orientation,
   dimensions, and relevant uncertainty before building?
2. **Specify:** Did it state the intended delta, invariants, assumptions, and
   checks precisely enough to reject a wrong result?
3. **Build:** Was the operation local and bounded, or did it reconstruct,
   globally transform, approximate, or leave the model unchanged?
4. **Validate:** Is there separate evidence for artifact health, requested
   intent, and preservation—not just a claim that the command succeeded?
5. **Deliver:** Is the submitted file the exact validated artifact, with no
   stale result, ignored failed check, or reduced-profile override?
6. **Evaluate:** What official paired score delta resulted, what behavioral
   cause explains it, and what cost, time, tool failure, or infrastructure
   effect could confound that explanation?

Classify the outcome as correct bounded edit, plausible partial edit,
unchanged/no-op, destructive or mistargeted edit, invalid artifact, or no
artifact. The deliverable from this study is not another average: it is one
causal account per fixture, the product affordance that would make the good
behavior repeatable, and a regression fixture/test for it. Specifically test
whether `209`/`224`/`225`/`238` improved through target identification and
preservation, whether `212`/`230` regressed because extra process caused
over-caution or abandonment of a correct direct path, whether `202` requires a
new preservation primitive, and whether `250` improved validity without
achieving semantic intent.

### 4. Instrument the workflow, not just the result

Measure stage adoption directly: target found, contract present, bounded
build, intent evidence, preservation evidence, failed-check repair, and exact
checked artifact delivered. Save these observations in a machine-readable
evidence bundle tied to run, fixture, artifact, and configuration hashes. This
turns Evaluate into a product feedback loop rather than a one-off transcript
review.

### 5. Additional value still on the table

- Promote the eight high-signal fixtures into a fast regression suite that runs
  before an 81-fixture benchmark.
- Add repeat runs or paired bootstrap intervals before presenting small score
  differences as model or workflow effects.
- Preserve best/worst previews and concise fixture narratives for the release;
  aggregate scores alone hide the difference between a valid no-op and a
  correct bounded edit.
- Investigate the workflow's latency and token cost, especially generation,
  and shorten instructions that add procedure without evidence.
- Use the Nano/Luna/Opus cost-quality curve to explore routing: inexpensive
  models for straightforward builds, stronger models or independent validation
  for ambiguous edits.
- Make every future benchmark emit its run manifest, stage evidence, artifact
  hashes, costs, timings, and infrastructure incidents automatically. The
  accompanying evidence appendix preserves these facts for the current effort
  because its raw logs may disappear.

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
