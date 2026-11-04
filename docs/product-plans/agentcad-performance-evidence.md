# AgentCAD performance effort: evidence appendix

This appendix preserves the evidence behind the September–October 2026
performance effort. Raw harness logs were treated as ephemeral. The facts
needed to reproduce the configurations, interpret the results, and design the
next experiment are recorded here.

## Experiment provenance

All five runs used the pinned CADGenBench dataset revision
`f76f965585817c621d6ea0d150d745adf670e66e` and 81 fixtures. K and L used
GPT-5 Nano at low thinking effort, a 900-second trial timeout, a 120-second
command timeout, concurrency two, and the strict AgentCAD CLI. Their evaluator
was the published AgentCAD 0.4.1 environment on Python 3.12.

| Candidate | Configuration | Product source | Wheel SHA-256 | Injected skill SHA-256 |
|---|---|---|---|---|
| K | GPT-5 Nano, standard AgentCAD | `2a377583d9d250971a584acb7b5ac6e2a75737c0` | `776cd55bc47f4b6b3fecacb6eba710991dae69d984b7435bd7147b9daeccc537` | `af663101ad32b4b8bd7a39e135ccdbe163820b711ead35698ff0c30b4d1d3b8f` |
| L | GPT-5 Nano, five-stage AgentCAD | `17d94510b83c494eeb75d9f97b8c46b6a1d97ca1` | `30e3c358fc68d5f3b33715fa1ed4f0091ee7b5c22d85665330c4d0eda32dbe82` | `798cf91f8b86f597c59dfa28911c2ea91013886a701506ca1c38bea6474a2a85` |
| M | GPT-6 Luna, standard AgentCAD | `2a377583d9d250971a584acb7b5ac6e2a75737c0` | Same product configuration as K | Standard help, 358 lines / 19,579 bytes |
| N | GPT-6 Luna, five-stage AgentCAD | `17d94510b83c494eeb75d9f97b8c46b6a1d97ca1` | Same product configuration as L | Five-stage help, 491 lines / 24,921 bytes |
| P | Claude Opus 5.5, standard AgentCAD | Same release candidate as K/M | Same product configuration as K/M | Standard help |

K and L differed only in the injected documentation/skill packaging; the
five-stage treatment contained approximately 29% more words. M and N used the
same corresponding configurations. P did not test the five-stage treatment.

## Official results

| Candidate | Report | Overall | Generation | Editing | Validity |
|---|---|---:|---:|---:|---:|
| K | [Nano, standard AgentCAD](https://huggingai4engineering-cadgenbench.hf.space/reports/james-dillard_gpt-5-nano-candidate-k-pi-harness-contro_20261001-022034.html) | .217 | .156 | .310 | 96.3% (78/81) |
| L | [Nano, five-stage AgentCAD](https://huggingai4engineering-cadgenbench.hf.space/reports/james-dillard_gpt-5-nano-candidate-l-pi-harness-five-s_20261001-001100.html) | .245 | .175 | .353 | 98.8% (80/81) |
| M | [Luna, standard AgentCAD](https://huggingai4engineering-cadgenbench.hf.space/reports/james-dillard_gpt-6-luna-candidate-m-pi-harness-contro_20261002-094716.html) | .329 | .301 | .373 | 97.5% |
| N | [Luna, five-stage AgentCAD](https://huggingai4engineering-cadgenbench.hf.space/reports/james-dillard_gpt-6-luna-candidate-n-pi-harness-five-s_20261002-094803.html) | .340 | .303 | .397 | 100% |
| P | [Opus 5.5, standard AgentCAD](https://huggingai4engineering-cadgenbench.hf.space/reports/james-dillard_claude-opus-5-5-candidate-p-pi-harness-s_20261002-094853.html) | .447 | .352 | .593 | 97.5% |

The [published AgentCAD baseline](https://agentcad.dev/measuring-agentcad-effectiveness)
was `.184` overall, `.142` generation, `.249` editing, 82.7% validity (67/81),
9.64 million tokens, and $0.231 model cost. Its direct-build123d editing score
was `.279`.

Candidate L is the release result. Candidate K is the intermediate waypoint
that separates accumulated product fixes from the additional workflow/help
treatment. Relative to the published AgentCAD result, L improved overall score
by 33.2%, generation by 23.2%, editing by 41.8%, and valid output count by 13,
while model cost declined 8.7%.

## Cost, tokens, and timing

| Candidate | Model cost | Tokens | Sum of fixture time | Median fixture time |
|---|---:|---:|---:|---:|
| K | $0.19084 | 7,697,379 | 8,587 s | 45.2 s |
| L | $0.21095 | 8,928,139 | 9,444 s | 91.6 s |
| M | $0.4656 selected / $0.4761 including discarded retry | 15.08M | — | 48.0 s |
| N | $0.4867 | 17.18M | — | 72.0 s |
| P | $18.558 | Not retained | approximately 75.8 min wall time | — |

L's generation median rose from 34.1 seconds in K to 78.2 seconds; editing
median improved from 194.1 to 166.8 seconds. M and N took approximately 58:47
and 75:54 wall time including recovery. P cost roughly 38 times as much as N.

## What changed in agent behavior

### Nano: K versus L

| Behavior | K | L |
|---|---:|---:|
| Explicit inspection | 2 | 7 |
| Inspection before first build | 0 | 6 |
| `measure`, `check-spec`, explicit `diff`, or written spec | 0 | 0 |
| Final answer claimed validation | 66 | 73 |
| Original input submitted | 4 | 4 |
| Obvious pass-through edits | at least 13/32 | at least 15/32 |
| Run attempts | 222 | 246 |
| Dry runs / real runs | 100 / 122 | 66 / 180 |
| Failed runs | 89 | 123 |
| Failed fixtures | 49 | 59 |
| Failure followed by meaningful output | 42 | 56 |
| Fixtures with no successful run | 7 | 4 |
| Rejected CLI calls | 6 | 1 |

L recovered from more command failures and completed two additional outputs,
but prose did not make Nano use the intended evidence tools. Examples of valid
but semantically weak behavior included re-exporting unchanged inputs, global
scaling, and approximating a local cap with a bounding-box operation.

### Luna: M versus N

| Behavior | M | N |
|---|---:|---:|
| Inspect before build, editing | 32/32 | 32/32 |
| Measure before build, editing | 27 | 22 |
| Render before build, editing | 16 | 25 |
| Requirements, generation | 0 | 48 |
| Requirements, editing | 0 | 5 |
| Written spec, generation | 0 | 33 |
| Written spec, editing | 0 | 0 |
| `check-spec`, generation | 0 | 11 |
| Explicit diff | 0 | 0 |
| Tool calls / AgentCAD commands | 574 / 309 | 765 / 351 |
| Run failures / attempts | 34 / 162 | 49 / 182 |
| Strict CLI rejections | 17 | 12 |

N beat M on 33 fixtures, lost on 37, and tied on 11; the median paired delta
was zero. The aggregate improvement was real but concentrated. Text guidance
made generation much more procedural, while editing still rarely produced a
contract or direct preservation evidence.

## High-signal fixtures

These fixtures should become the diagnostic panel for workflow changes.

| Fixture | M standard | N five-stage | P Opus standard | Why it matters |
|---|---:|---:|---:|---|
| 209 | .305 | .731 | .731 | Strong target-identification and thickening win. |
| 224 | .387 | .832 | .803 | Strong bounded-edit/correction win. |
| 225 | .236 | .906 | .893 | Largest workflow gain; target selection appears decisive. |
| 238 | .342 | .778 | .342 | Workflow-specific gain not explained by model quality alone. |
| 212 | .973 | .398 | .973 | Five-stage regression: over-caution/fallback produced a no-op. |
| 230 | .689 | .210 | .549 | Workflow regression against an otherwise viable direct path. |
| 202 | .060 | .060 | .000 | Source-preservation hard case; Opus produced no valid artifact. |
| 250 | .000 | .321 | .000 | Workflow rescued validity, but semantic quality remained modest. |

Other notable cases were 204, 214, and 245, where destructive or mistargeted
changes reduced the score; and 242/243, where unchanged geometry sometimes
outscored a bad edit. In fixture 118, N submitted after `check-spec` reported
nine required holes but found 17. In P fixtures 202 and 250, deliverable
validation rejected the geometry, after which the agent selected a reduced
kernel-only profile and submitted anyway.

## Known experimental limitations

- Each condition was run once, so the observed deltas include sampling noise.
- Conditions were run sequentially. L encountered late disk exhaustion, making
  infrastructure state a potential confound.
- A retry for M fixture 202 reran the model instead of evaluating the frozen
  artifact. The selected output differed from the original no-op output and
  should not be treated as a clean paired observation. The original artifact
  SHA-256 was
  `661210dec702f8347603884bec0476da0a1af5376a55bf842c45ba25181e2860`;
  the selected retry was
  `b11701dc71732fd708f2ef55d5e5288ce1a5692a5607ebb342cc9e7fb4724d2f`.
- STEP byte changes were used as an edit proxy even though re-exporting the
  same geometry changes bytes. Behavioral no-op counts are therefore lower
  bounds.
- Candidate P is standard AgentCAD, not the five-stage release treatment, and
  there is no matched Opus 5.5 direct-build123d control. It cannot establish
  AgentCAD's lift on Opus.
- The external Opus 5.5 xhigh result is useful context but differs in effort and
  harness configuration; it is not a valid control for a marketing claim.

## Submitted package archive manifest

The following exact archives were placed on the submitter's desktop. The
hashes allow a future copy to be authenticated even if transient logs and
caches are deleted.

| Archive | Bytes | SHA-256 |
|---|---:|---|
| `gpt-5-nano-candidate-k-pi-harness-control-2026-09-30.zip` | 17,874,281 | `4ebf8953ad654da07c58cbf10cfe82db71ce4a66a7c4110632c4212ff776beb4` |
| `gpt-5-nano-candidate-l-pi-harness-five-stage-workflow-2026-09-30.zip` | 22,304,869 | `d3414511c86fc31cf1802ce6abca4eb66ed163f47ea161667af78190087747b3` |
| `gpt-6-luna-candidate-m-pi-harness-control-2026-10-01.zip` | 26,582,834 | `8bb1b2c35700631b8fc42d977fe61e5c03a437b4beef9bd8ad95615fa04084b3` |
| `gpt-6-luna-candidate-n-pi-harness-five-stage-workflow-2026-10-01.zip` | 28,058,332 | `a74196da9979f537af6318960eb97fa29b568b8a9ad3b9728ffaf2b9636271a4` |
| `claude-opus-5-5-candidate-p-pi-harness-standard-2026-10-01.zip` | 32,301,066 | `25a812bf1296254acdb2c73742d284c9259c2b3ffe8aff036de58c692ccf8f88` |

## Permanent takeaways

The score reports and archives establish output quality. The behavioral review
establishes why the scores moved: workflow prose improves recovery and can
focus editing, but it does not reliably create specifications, invoke evidence
tools, or prevent delivery after a failed check. The next product step is to
make those stages observable and executable; the next evaluation step is a
matched direct-versus-AgentCAD comparison with interleaved conditions and
frozen-artifact recovery.
