# Default inspection bundle

Import includes a compact `inspection` report and prepares a ranked image
bundle. Its report summarizes validation quality, provenance, units, model
dimensions, reusable part definitions and occurrence counts, and raw topology.
The complete part list and sample measurements/reasons live at
`outputs.inspection_report`; the complete media catalogue is at
`outputs.inspection_bundle`. Every entity remains in the durable
`geometry_manifest.json`.

Successful saved `run` versions now issue the same typed-ID manifest from
their delivered STEP before optional graphics. Use the returned
`outputs.step` with `inspect --report`, `--id ID`, or `--image` without
re-importing. Reports and inspection sheets are prepared on request; run's
existing preview controls still apply. Dry runs, script failures, and invalid
geometry do not issue IDs. Inventory failure or timeout preserves the saved
STEP, script, and successful version, with explicit artifact status.
Recovered timeout responses retain the phase-specific suggestion. An inventory
timeout also returns an executable `inspect <saved STEP> --ids` action for legacy
inspection, including the selected build root; it does not create another version.

Generated provenance records the archived script's filename/hash and runtime.
Generated report quality is adopted only when the saved run's version,
manifest ID, and delivered baseline hash match. Part names and hierarchy come
from the exported STEP and are marked as derived output metadata, rather than
recovered supplier structure or a mapping from script display IDs. `run.parts`
still describes script display objects; inspect's typed IDs address normalized
geometry. Each saved version issues fresh entity numbers. Matching numbers in
two versions do not establish correspondence; use the new version's IDs to edit
its geometry. Older versions without manifests retain the legacy integer-ID
path, or can be imported as a new baseline; inspection never silently reissues
an existing mapping.

This release reports named recognition as `not_available`, with
`recognized_feature_count: null`. Sampled faces and edges are raw topology,
not identified holes, pockets, bosses or fillets. A completed raw report does
not claim that named recognition ran or found zero features.

## Images and selection

The default bundle contains one overall four-view sheet, up to four part
samples and up to four face/edge samples, with at most nine distinct PNGs.
All are 1024 × 1024 with iso/top/front/right views, hard dimensions in mm and
canonical ID labels. Sample images are isolated in assembly coordinates;
contextual highlighting remains available through `--id ID --image`.

Parts rank by placed volume, including repeated occurrences, with preference
for varied measured dimensions. Faces rank by area weighted by source-part
repetition; edges rank by length weighted by repetition. Selection prefers
varied measured sizes and endpoint spans, with deterministic ID tie breaks.
These measurements guide representative sampling; they do not prove shape
equivalence or semantic feature recognition. Selection reasons and repetition
counts are available in the saved analysis.

Edges whose recorded length is zero are excluded from representative samples
and overall sample labels. Remaining slots use other faces where available,
or the bundle returns fewer samples. These edges retain their IDs in the
complete inventory and remain available through explicit lookup.

Priority is overall, then alternating part and face/edge sample rank. A sole
part definition can reuse the overall image when its memberships cover every
model entity. The overall sheet labels that part ID, and its sample explicitly
states `image_id: overall` and `image_mode: overall_reuse`. For a repeated part,
this image covers all its placements. To inspect one placement, request its
occurrence ID. Part-list definition dimensions use `definition_local`; sample
and image dimensions describe the placed geometry in `assembly` coordinates.

Report `media[].png` filenames resolve against `inventory.image_root`. This
avoids repeating long paths for every image. The full bundle catalogue retains
complete paths. MCP returns actual PNG image content blocks in the same order
as the report's media array, with hashes checked before attachment. CLI and
sidecar paths refer to the execution filesystem.

## Retrieve, reduce or omit

Use actual IDs returned for your imported version:

```sh
agentcad import supplier.step --init --no-view --no-diff
agentcad inspect v1_supplier/output.step --report
agentcad inspect v1_supplier/output.step --report --max-images 2
agentcad inspect v1_supplier/output.step --report --no-images
agentcad inspect v1_supplier/output.step --report --text-budget 1500
agentcad inspect v1_supplier/output.step --id edge_v1_17 --image
```

On import, `--no-report` omits inline report text while retaining saved
analysis and the prepared bundle. `--no-inspection-image` skips inspection
media while keeping the report. `--max-images 0..9` caps media preparation;
later report/image retrieval can prepare omitted samples. A report-off import
still returns bounded import status and artifact references.

MCP `inspect(report=True, report_images=False, max_images=..., text_budget=...)`
exposes the same operation. Image-free requests retain the same analysis
revision, selected IDs and planned aliases. Optional inline detail can differ
because image references also consume the text allowance. Media failures are
explicit in `media_summary` and the full catalogue; they preserve core geometry,
the ID registry, the report and edit scaffold.

MCP `import_cad(file=..., cwd=..., initialize=True)` delivers this bundle
directly from a local STEP/STP/BREP import: the same CLI JSON plus transport
`_exit_code`, one text block and the actual PNGs in `inspection.media` order.
The maximum is nine distinct image blocks. `initialize` maps to `--init`;
`runtime`, `validation_profile`, `build_dir`, `view` and `diff` mirror CLI
controls. `report=False` omits inline inspection and its attachments while
retaining prepared artifacts. `report_images=False` or `max_images=0` keeps
the report without inspection image blocks. Legacy preview/viewer artifacts
follow their existing controls. `text_budget` covers only report preparation;
whole-response delivery has its own default limit. `full_response=True` returns
uncompacted command records without changing the import or image plan.

Every attachment is checked against its declared hash and PNG signature.
If delivery fails, the import response retains its successful core status,
reports `image_delivery.status=failed` with zero attached images, and supplies
an executable inspection retry action retaining the text/image limits. It does
not repeat the import or issue new IDs. Source, scaffold and artifact paths refer to the MCP execution
filesystem; inline PNGs are delivered to the caller. `cloud_import` remains a
separate hosted-project upload operation. Local import needs no account.

## Text and context limits

The fallback allowance is **2,000 estimated tokens for the complete JSON
response**, using `ceil(emitted JSON UTF-8 bytes / 3)` plus a 32-token transport
reserve. Both import and `inspect --report` return a nested `inspection` object
and outer `delivery` audit. Repeated metadata, optional detail and tail records
are omitted before essential status, counts, IDs and unfinished-job recovery.
Returned/total/truncation counts are explicit. The complete pre-delivery command
response remains available through the exact `response_snapshot.retrieve` action;
the saved analysis and durable manifest contain the full inventories.

`--full-response` (MCP `full_response=True`) opts out of this projection and
retains the earlier flat report shape. Saved response retrieval is complete by
default. Image count/resolution caps still apply; unknown image cost is stated
explicitly. Caller context and vision estimates can enable a 5% combined target.
No universal context or vision-token guarantee is claimed. See
[delivery budgets](../inspection-budgets.md) for the estimation and override
contract, including explicit failure when minimum evidence cannot fit.

## Reuse and identity

Analysis is keyed by baseline manifest identity, report revision and the stored
validation summary, adopted only when import version and original-source hash
match the saved manifest. A neighbouring file's validation is not substituted.
Cached analysis is checksummed and verified against the
baseline/kernel before reuse. Replays do not repeat shape analysis or issue new
entity IDs. Missing images reuse one checked geometry context across the bundle.
Image publication rechecks the baseline, and cached PNG/metadata hashes remain
verified. Corrupt derived artifacts regenerate; source geometry and issued
memberships are never rewritten by report preparation.

The saved report is independent of delivery budgets. The media catalogue records
prepared and omitted samples, including failures, while a smaller response can
reference this full detail. Caller-specific media catalogues may differ without
changing the analysis revision or selected entities. Legacy and cloud-specific
commands retain their existing behavior; this adds no hosted analysis service.

## Measured slice check

### Default whole-response delivery check

The default whole-response rollout compared the agreed sample bundle with an
overall-only initial bundle on four paired targeting tasks, frozen before the
evaluators saw them. Both conditions used the same 2,000-token JSON fallback,
baseline IDs, and complete inventory retrieval. The tasks used CADGenBench 101,
the pump manifold, a 12-definition component library and a rotated nested
assembly. Targets included an unsampled small part and a particular placement.
These were held-out tasks, not previously unseen benchmark model files.

| Initial bundle | Correct targets | Follow-up information requests | Initial PNGs across four tasks |
|---|---:|---:|---:|
| Overall plus ranked samples, within the text limit | 4/4 | 14 | 21 |
| Overall only | 4/4 | 17 | 4 |

Both evaluators viewed the selected sheets and executed matching build123d or
native baseline selectors without changing baseline bytes. Counts include
selector verification, inventory retrieval and an unsuccessful `--full-response`
ID lookup in each condition. That option now acts harmlessly on already
unbounded ID lookup. The check also prompted clearer documentation: a response
snapshot restores the original prepared reply, while the saved analysis and
manifest hold the complete inventory. Parts/occurrences still require native
selection; this rollout does not add named feature recognition or selectors.

The sample is too small to establish superiority, and the extra images have an
explicit context cost. Image-token cost was unknown; no combined-token or
latency improvement is claimed. The unsampled and placement tasks passed in
both conditions, with no wrong-target regression, so the agreed image caps
remain unchanged. Callers can reduce initial images with `--max-images`.

The actual default CLI `inspect --report --no-daemon` was also measured against
a fresh copy of the existing 1,000-instance assembly (6,000 faces / 12,000 edges).
In one Python process, the first call took **4.05 seconds** and cached replay
**0.57 seconds**, within the predeclared 20s / 3s report smoke limits. Both
returned five images and **1,940 estimated JSON text tokens**, plus the 32-token
reserve. This includes default compaction and snapshot publication/retention;
baseline and manifest hashes stayed unchanged. Observed peak process RSS was
631 MiB, not a new memory pass/fail result. Environment: Apple M4, macOS 15.6,
Python 3.12, build123d 0.10.0 and cadquery-ocp 7.8.1.1.post1. Fixture copying,
initial source import/validation, MCP and cold-filesystem/cloud behavior were
excluded. These observations do not establish complete-import release gates.

### Initial report preparation

On 2026-10-27, the report operation on an existing normalized 1,000-instance
box assembly (6,000 faces, 12,000 edges) took **4.20 seconds** to create its
first bundle and **0.565 seconds** for verified cached replay. It returned
five distinct images: overall reused for the sole part definition plus four
raw face/edge samples. The compact report retained the part name and used
approximately 1,700 estimated text tokens. Reference environment: Apple M4, macOS 15.6,
Python 3.12.12, build123d 0.10.0, cadquery-ocp 7.8.1.1.post1.

The predeclared local smoke limits were 20 seconds first-bundle and 3 seconds
cached. These timings include analysis, selected image rendering, persistence,
baseline checks and response construction. They exclude initial source import,
geometry validation, legacy preview/viewer generation and MCP transport. They
are first-process/warm-cache observations, not cold-filesystem, peak-memory,
full import/report or model-context guarantees. The simple repeated-box fixture
does not represent a complex machined assembly.

Regression coverage includes both real CADGenBench and pump-manifold imports,
12 distinct part definitions with nine sample images, capped/image-free modes,
part/overall reuse, cached-context reuse, corruption/recovery and wrong-baseline
rejection. A separate fresh-agent workflow checked real MCP stdio attachments,
configured paths with spaces, retrieval of an unsampled ID and an actual
scaffold edit with independent sibling-preservation checks.

### Saved-version inspection follow-up

On 2026-10-28, a real CLI `run --no-preview --no-diff --no-view` loaded and
saved each fixture, then the report API prepared/replayed an image-free report
against its saved STEP. The same Apple M4 / Python 3.12.12 environment was used.

| Fixture | Saved topology | ID-manifest phase | First image-free report | Cached image-free report |
|---|---|---:|---:|---:|
| CADGenBench 101 | 150 faces / 427 edges | 0.083 s | 1.181 s | 0.003 s |
| 1,000 repeated boxes | 6,000 faces / 12,000 edges | 0.977 s | 1.458 s | 0.101 s |

Fresh CLI process totals, including script execution, validation, STEP export
and inventory, were 2.83 s and 4.66 s respectively. The report calls shared one
process; the first includes lazy CAD imports and subsequent calls reuse them.
Source fixture generation was excluded. Predeclared local limits of 5 s for
inventory and 3 s for first/cached image-free reports passed. These are single
process-local observations on warm filesystems, not cold-filesystem, peak-memory,
complex-assembly, graphical-report or remote-delivery guarantees. The saved
manifests were 0.30 MB and 10.66 MB; the full inventory stays out of inline text.
