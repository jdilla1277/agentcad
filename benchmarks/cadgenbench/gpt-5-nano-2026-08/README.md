# GPT-5 Nano CADGenBench evidence

This directory contains the public evidence behind the agentcad GPT-5 Nano
comparison published in August 2026. Two agents attempted the same 81
[CADGenBench](https://huggingface.co/spaces/HuggingAI4Engineering/CADGenBench)
fixtures:

- **Without agentcad:** GPT-5 Nano with a Pi harness and direct build123d
  access.
- **With agentcad:** the same model and Pi harness with the release candidate
  that became agentcad 0.5.1. It paired the published agentcad 0.4.1 CLI with
  the distributed agentcad skill.

## Headline results

| Measure | Without agentcad | With agentcad | Change |
| --- | ---: | ---: | ---: |
| CAD Score | 0.124 | 0.184 | +48.4% |
| Valid outputs | 27 / 81 | 67 / 81 | 2.48x |
| Generation CAD Score | 0.023 | 0.142 | +517.4% |
| Editing CAD Score | 0.279 | 0.249 | -10.8% |
| Total tokens | 10,912,951 | 9,644,897 | -11.6% |
| Estimated model cost | $0.295717 | $0.231134 | -21.8% |

The quality figures come from the hosted CADGenBench reports:

- [Without agentcad report](https://huggingai4engineering-cadgenbench.hf.space/reports/james-dillard_gpt-5-nano-candidate-a-pi-harness-2026-0_20260827-100706.html)
- [With agentcad report](https://huggingai4engineering-cadgenbench.hf.space/reports/james-dillard_gpt-5-nano-candidate-c-pi-harness-2026-0_20260828-001340.html)

CADGenBench uses validity as a hard gate: invalid or missing outputs receive a
CAD Score of zero. For valid generation outputs, the score combines shape
similarity, interface alignment, and topology. See the benchmark's
[metric documentation](https://github.com/huggingface/cadgenbench/blob/main/docs/metrics.md)
for the complete definition.

## What is included

- [`methodology.json`](methodology.json) records the model, candidate,
  evaluator, revisions, limits, and known limitations.
- [`control-summary.json`](control-summary.json) and
  [`agentcad-summary.json`](agentcad-summary.json) combine the hosted results
  with aggregate harness usage.
- [`control-trials.jsonl`](control-trials.jsonl) and
  [`agentcad-trials.jsonl`](agentcad-trials.jsonl) contain one sanitized record
  per fixture: outcome, classification, message counts, token counts, cost,
  and elapsed time.
- [`harness/`](harness/) contains the package, source, and tests from the exact
  harness revision used for each condition.
- [`verify_evidence.py`](verify_evidence.py) recalculates the execution totals,
  token costs, and outcome counts from the per-fixture records.
- [`checksums.sha256`](checksums.sha256) provides hashes for the evidence files.

Run the local consistency check from this directory:

```bash
python3 verify_evidence.py
```

## Cost calculation

Costs use the GPT-5 Nano rates in effect for these runs: $0.05 per million
uncached input tokens, $0.005 per million cached input tokens, and $0.40 per
million output tokens. Those rates are documented on the
[OpenAI model page](https://developers.openai.com/api/docs/models/gpt-5-nano).
The per-fixture costs are preserved as recorded by Pi and are independently
recalculated by `verify_evidence.py`.

## Timeouts and packaging

Neither condition exhausted the 900-second fixture limit. Each recorded one
harness-side submission-validation timeout at the 120-second command limit:

- Without agentcad, fixture 202's submitted STEP was preserved but later
  rejected by the CADGenBench sanity check as invalid.
- With agentcad, fixture 242 was retried and the recovered output passed the
  later validation and packaging checks.

The local harness totals therefore do not equal the hosted valid-output totals.
Harness completion means an artifact passed the run-time validator; the hosted
CADGenBench report is the authority for benchmark validity and quality.

## Interpretation limits

This is one run per condition, not a statistical study. The runs occurred on
consecutive days rather than in randomized order. They used the
`openai/gpt-5-nano` alias, because no immutable provider snapshot was exposed.
The experiment harness revision added the skill-loading and isolated-environment
plumbing needed for that condition; the exact sources for both revisions are
included for review. These results establish a useful directional result for
this model and setup, not that the effect generalizes to every model.

## CADGenBench verification

CADGenBench marks new entries as unvalidated until maintainers review public
methodology evidence. Its
[validation instructions](https://github.com/huggingface/cadgenbench/blob/main/docs/benchmark/validation.md)
ask submitters to email `michael.rabinovich.27@gmail.com` with the subject
`CadGenBench verification` and a pointer to the evidence. This directory is
intended to serve as that pointer.
