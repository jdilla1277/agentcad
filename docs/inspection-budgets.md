# Caller budgets for inspection delivery

Import and report inspection accept optional budgets for the **complete JSON
response and selected image bundle**, including import metadata and job fields.
They change delivery without importing again, restarting analysis or changing
geometry IDs. Existing calls without these controls keep the legacy 2000-token
nested-report allowance and image caps.
With caller budgets, report responses use an outer `delivery` audit and nested
`inspection` report. This keeps the whole-response and report estimates separate;
ordinary report calls without caller limits retain their existing flat shape.

```sh
agentcad import supplier.step --init --idempotency-key supplier-001 \
  --context-window 128000 \
  --response-text-budget 3000 \
  --vision-profile '{"name":"my-host","tokens_per_image":1000}'

agentcad inspect v1_supplier/output.step --report \
  --response-budget 5000 --image-budget 2000 \
  --vision-profile '{"name":"my-host","tokens_per_image":1000}'
```

`--context-window` derives a combined target of floor(context / 20), about 5%.
An explicit `--response-budget` replaces that target. `--response-text-budget`
is a separate whole-JSON ceiling; when omitted, the combined limit is also the
text ceiling if known, otherwise the whole-text fallback is 2000. The existing
`--text-budget` remains a separate cap on the nested report. The strictest
applicable limit wins.

Reserve text for identity, core/acceptance status, dimensions, counts, progress
and retrieval first. Then use the remaining combined allowance for images,
respecting `--image-budget` too. Keep the existing priority order: overall,
then alternating part and raw face/edge samples. Omit images from the tail.
Without a combined/image-token constraint, unknown image cost still allows
the existing count/resolution caps. With either token constraint and no vision
profile, return image references and IDs without inline images, explaining
`vision_profile_required`. A zero image budget explicitly selects no images.
Omission never means that a physical feature is absent; recognition coverage
continues to be reported separately.

## Estimates and limits

A vision profile is a caller-supplied fixed estimate for one 1024 × 1024 sheet:
`{"name":"my-host","tokens_per_image":1000}`. Its cost is not an AgentCAD claim
about any model's tokenizer, billing or resizing. Profile names are labels;
no service is contacted. Profile fields are closed and cost must be a positive
integer. Choose the estimate your host applies to these sheets.

Text is estimated as ceil(the emitted JSON's UTF-8 byte length / 3), including
JSON spaces/escapes. A further 32 estimated tokens are reserved for small
transport fields. MCP recomputes after adding its exit code and after an image
attachment failure. Costs describe one JSON response and its selected images;
they exclude provider-specific tool framing, duplicated structured transport
representations and image base64 encoding on the wire. CLI reserves the image
allowance for the selected media references; MCP attaches those checked PNGs.

`delivery` reports requested/applied limits, the source of the combined target,
actual estimated text/image/combined costs and `budget_satisfied`. Its media
summary distinguishes prepared images, eligible references, returned images
and newly omitted IDs. Earlier preparation/report omissions remain in the
report's own media summary and saved catalogue.
The nested report retains its preparation budget; `delivery` contains the
caller-specific image estimates. Acceptance receipts and verbose
checks can be summarized with their decision/revision keys intact. The full
original response remains available through `response_snapshot`.

An allowance smaller than required status/identity/retrieval returns
`delivery.status=budget_unavailable` and `budget_satisfied=false`, with no inline
images. It does not claim the limit was met or undo accepted geometry. Raise
the limit or follow the snapshot retrieval action. A snapshot storage failure
similarly reports `snapshot_unavailable`; the original command result survives.

## Retrieve the full response

Every budgeted response references a complete pre-budget snapshot containing
the original JSON and its original path base. Copy its exact retrieval action:

```sh
agentcad inspect /project/v1_supplier/output.step --report \
  --response-snapshot delivery_RETURNED_ID --build-dir /project
```

Retrieval runs no CAD work and preserves the original exit code. It returns
`historical_response=true`, `snapshot_command`, absolute artifact paths and the
recorded operation's data. `current_advanced` is false for this read;
`recorded_current_advanced` describes the original operation. The import/job
progress is historical too; follow the returned job status action for fresh
progress. Snapshot retrieval expands the saved report allowance as needed to
preserve its full selected records after path formatting. Add new caller
limits to request another bounded projection of the same snapshot.
Recovered inspection, measurement and view actions resolve their CAD file from
the saved response's directory and include the selected build root, so they can
be followed from another working directory. Failed imports retain their nonzero
exit code through retries and snapshot retrieval, including preflight failures.

Snapshots are hash-addressed JSON files under the selected build root's
`.agentcad/inspection_delivery`. Retrieval checks their integrity, project/build
root, baseline path and baseline bytes. Missing/changed evidence fails
explicitly. Full snapshot retrieval is local; remote transfer, age/byte quotas
and a possible migration of the legacy default to whole-response limits remain
separate work. These controls can also be used with inspection jobs and import
retries; new delivery limits are outside both identities.

## MCP

The same fields are available on `import_cad` and `inspect`:

```python
import_cad(file="supplier.step", cwd="/project", initialize=True,
           idempotency_key="supplier-001", context_window=128000,
           response_text_budget=3000,
           vision_profile={"name": "my-host", "tokens_per_image": 1000})
inspect(file="/project/v1_supplier/output.step", cwd="/project", report=True,
        response_snapshot="delivery_RETURNED_ID", build_dir="/project",
        response_text_budget=3000, image_budget=0)
```

MCP attaches only images named by the final bounded report, validating PNG bytes
and hashes. Status-only job polling and image-free reports remain image-free.
