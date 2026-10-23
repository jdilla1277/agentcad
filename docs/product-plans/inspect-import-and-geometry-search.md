# Product requirements: inspect imported geometry and search it

**Status:** Draft for product review

**Workflow stage:** Inspect, before an agent edits an existing CAD model

## Summary

An agent importing an existing CAD file should immediately understand what was
loaded, how trustworthy the geometry is, what parts and addressable geometry
exist, and how to inspect or edit a specific region. The default response is a
compact manifest plus selected four-view images. The full inventory and further
images remain available by ID.

The agent can then search across parts in a selected version of the current
project using explicit filters or find geometry similar to a selected part or
feature. Results carry measured evidence, highlighted images, version-local
IDs, and code selectors that all refer to the same geometry. CLI and MCP expose
the same product contract when AgentCAD runs locally, in the cloud, or through
remote mode.

build123d is the reference editing backend. The first useful release connects
import, a labeled image, ID lookup, and a runnable edit after geometry reload;
later milestones add recognition and similarity to that working handoff.

This effort includes a required milestone for recognizing named design
features such as holes, slots, pockets, bosses, and fillets. Geometric grouping
is an intermediate deliverable. Completion means delivering the strongest
Inspect experience that available open-source technology supports and that we
can demonstrate reliably on imported geometry.

This effort uses open-source components throughout. Local recognition must
run with open-source code and appropriately licensed model assets. Commercial
engines or paid services may be considered for a future paid cloud offering.

Built-in natural-language search is outside this PRD. A calling agent can turn a
user's words into structured filters and a similarity reference.

## Problem

Today, `import` creates a versioned baseline and an `edit.py` that loads the
normalized STEP. `inspect --ids` exposes addressable solid, face, and edge IDs;
`measure` reports dimensions and cylindrical geometry; `render` produces views.
These are useful but require the agent to assemble its own picture from several
calls and large topology lists. Import does not return a visual inventory linked
to those IDs. The STEP loader joins multiple roots into a compound, while the
version record does not retain supplier assembly names as named parts. Search is
limited to specific measurement filters rather than a general query over the
imported model.

The result is a slow and error-prone first step for an edit: an agent can see a
promising face in a render yet still have to work out which inspection record
and code selector identify it.

## Goals

1. Make a newly imported solid model understandable from one default response.
2. Give every reported part, face, and edge a version-local ID shared by
   inspection, images, search, and code selection.
3. Make the complete part and geometry inventory available without filling the
   agent's context with every record or image.
4. Let an agent filter geometry by measurable properties and spatial scope.
5. Let an agent provide a part or feature as an example and retrieve similar
   results, optionally narrowed by filters.
6. Give equivalent capabilities and response fields through CLI and MCP.
7. Support local, cloud, and remote execution, including retrieval of every
   reported manifest, image, inspection record, and edit baseline.
8. Recognize meaningful design features, connect them to measured geometry and
   editable IDs, and establish the achievable recognition quality through
   evaluation of available open-source technology and real imported models.

## Scope and terms

This effort targets STEP/STP and BREP solid geometry supported by the existing
import path. A **part definition** describes reusable intrinsic geometry. An
**occurrence** is one placed instance of a definition in an assembly, with its
own ID, parent occurrence, assembly path, and placement. Ten instances of the
same bolt are one definition and ten occurrences. When source structure is
absent, label the derived definitions and occurrences explicitly.

A **topology entity** is an addressable solid, face, or edge. Occurrence-level
entity IDs distinguish the same intrinsic surface in different placed
instances. A **candidate feature** is a geometric grouping inferred from one
or more entities, such as a cylindrical region; it is not a recovered
parametric feature-tree operation.
A **recognized feature** assigns a supported design-feature type to a measured
region, such as a counterbored hole, with its dimensions, member entities,
and recognition evidence.

Part and topology IDs are scoped to the normalized geometry of one version.
Their version component changes after an edit; the entity number is retained
when correspondence is unambiguous. Candidate-feature groups also name their
member topology IDs. Cross-version correspondence is best effort, and temporary
search-result groups have no continuity guarantee.

The source file is retained for provenance. Measurements, IDs, renders, search
results, and `edit.py` selectors derive from the same normalized version
geometry that the edit script loads. A durable geometry manifest preserves
that mapping. Rebuildable analysis and search indexes reference this manifest
and never redefine its IDs.

An **analysis revision** is an immutable record of recognition software,
configuration, source evidence, feature memberships, and results for a geometry
version. A **search snapshot** pins the query, geometry manifest, analysis and
index revisions, ranking method and budget, ordered results, and coverage.

## User journey

1. The agent imports a STEP or BREP file. AgentCAD records source provenance,
   validates the geometry, and prepares the inspection bundle.
2. The default response says whether the import is usable, gives whole-model
   dimensions and an overall four-view image, lists the parts and geometry
   inventory, identifies recognized design features with their measurements
   and evidence, and shows selected part and feature images.
3. The agent follows an ID from the image into an inspection record. It can ask
   for the isolated four-view image and measurements of any other ID.
4. The agent filters a part or region, or asks for geometry similar to a
   selected ID. Search returns ranked, explained results and more-result paths.
5. The agent uses the returned selector against the imported baseline in
   `edit.py`, then performs separate edit validation.

## Requirements: import and default inspection

### I1. Quality and provenance

- Record original filename, source hash, detected format, source units when
  recoverable, normalized version path, and the exact baseline used for IDs.
- Report parse, kernel validity, shell closure, and mesh manifold findings as
  applicable. State warnings and partial coverage rather than treating a
  parsed file as necessarily ready for a solid edit.
- Preserve source assembly hierarchy, component names, and placement when the
  source format provides them. If names or hierarchy are absent or cannot be
  recovered, say so and label derived parts accordingly. Do not silently
  present a multi-root supplier assembly as one named part.
- Commit the usable baseline, durable geometry manifest, and configured edit
  scaffold before recognition and rendering. Enrichment failure or
  timeout preserves these artifacts and reports the incomplete stages.
- Keep measured geometric facts distinct from inferred design purpose. A
  cylindrical surface is not automatically a hole.

### I2. Geometry inventory and identity

- Provide a complete machine-readable part list and complete indexed inventory
  of addressable solids, faces, and edges for the imported version.
- Each record includes the parent part, kind, relevant hard dimensions,
  orientation, location or bounding box, measurement units, and an inspection
  and image retrieval path. Include source name and provenance where available.
- Identify both the part definition and occurrence for placed geometry. Reuse
  intrinsic recognition per definition where possible, then map its evidence
  to distinct occurrence-level entity and feature IDs in the baseline.
- Report definition and occurrence counts separately. Search results and
  counts default to occurrences; explicit grouping by definition may collapse
  repeats while retaining occurrence IDs and counts. Deduplicating an image
  or cached analysis never removes an occurrence from the inventory.
- Spatial filters use assembly coordinates by default. Each placement and
  measurement names its frame; intrinsic measurements use definition-local
  coordinates. A query using a local frame identifies the occurrence and its
  transform. The normalized-baseline mapping preserves nested placements.
- An occurrence selector identifies only the selected instance, including
  after reload. Editing a reusable definition across all occurrences requires
  an explicit selection/action. Shared geometry is not permission to edit
  every instance of that geometry.
- The renderer, inventory, search, and `pick_face`/`pick_edge` helpers use one
  mapping for each version-local topology ID. Every visual callout resolves to
  exactly the entity named in the machine-readable record and code selector.
- Candidate-feature groups expose their member IDs and the evidence for the
  grouping. An unrecognized region remains accessible as faces and edges.
- The foundation milestone uses conservative geometric grouping alongside raw
  entities. Group adjacent faces only when an explicit geometric rule
  establishes their relationship, such as contiguous patches of the same
  cylindrical surface. Report the rule and member IDs. Sharing a diameter or
  axis alone does not establish that two regions are one feature.
- The named-feature recognition milestone builds on this inventory and must
  be delivered before this effort is considered complete.

### I2.1. Object IDs and continuity between versions

- Public IDs encode object type, version, and an entity number: `part_v1_1`
  names a definition, `occurrence_v1_2` names an instance, and `face_v1_55`,
  `edge_v1_17`, and `feature_v1_3` name addressable geometry. Compact aliases
  such as `F55` may appear in images when the version is also labeled. Records
  include the canonical version identifier and baseline hash to disambiguate
  versions from different projects.
- Keep each ID stable within its version, including after geometry reload.
  Try to retain the entity number in the next version when there is
  unambiguous evidence that it is the corresponding entity: `face_v1_55` can
  become `face_v2_55`. Use operation history when available, then conservative
  geometric matching. Record the correspondence and its evidence.
- Ambiguous matches, splits, and merges receive new numbers with explicit
  lineage where known. Retaining a number does not promise that geometry or
  dimensions are unchanged. Public numbers resolve through the version's ID
  map and need not equal the kernel's traversal indices. References from the
  wrong version are rejected rather than silently remapped.

### I2.2. Identity when analysis changes

- The geometry manifest and public entity-to-baseline mapping are durable and
  immutable for a geometry version. Rebuilding an index cannot renumber them.
- Recognition upgrades and configuration changes create new analysis
  revisions. Freeze the member entity IDs of each public feature ID when it
  is issued. A revision that changes membership creates a new feature ID,
  with correspondence where known; it cannot retarget an old feature ID.
- Persist issued feature IDs and their original memberships in a durable,
  append-only identity registry, independent of rebuildable analysis indexes.
  Analysis revisions reference these entries; rebuilding recognition cannot
  reissue an old number for another group.
- Reports, measurements, images, and search snapshots identify their analysis
  revision. A reclassification with unchanged membership may reuse the feature
  ID, but the old report retains its original classification and evidence.
- Old references resolve to their original entities and memberships. If the
  referenced revision or artifact is no longer retained, return an explicit
  unavailable/stale response and a recovery action. Never silently substitute
  a newer feature membership, report, or search page.

### I3. Default response and images

- Show the import report by default. Provide an explicit option to turn it off
  in CLI and MCP. The imported version and inspection bundle remain available
  when the report is turned off.
- The initial agent response includes quality, provenance, units, whole-model
  hard dimensions, part and feature counts, a compact ID/name list of parts,
  a geometry inventory summary, selected IDs, and paths to the complete lists
  and image bundle. If the part list exceeds the configured context budget,
  report the count, explicit truncation, and path to the complete list.
- Deliver one overall four-view image, up to four sample part four-view images,
  and up to four sample feature four-view images in the initial response.
  Rank samples by importance: favor prominent geometry, repeated geometry,
  and geometric variety rather than topology traversal order. Make the
  selection deterministic and explain the main reasons for each sample.
  Reuse the overall image when a one-part model has identical
  whole-model and part geometry.
- Prepare the default samples during import. Overall + four parts + four
  features delivers at most nine distinct images, within a default ceiling
  of 10 images. Every other part, face, edge, and candidate feature has
  an image retrieval action that renders its isolated or highlighted four-view
  on request. Each four-view includes isometric, top, front, and right views
  with a common coordinate frame and visible ID labels.
- The first response targets roughly 5% of the calling agent's context budget.
  Apply the explicit budget contract in I3.3. Large inventories and non-sample
  images remain artifact references. The agent can request an expanded or
  image-free response.
- The default response explains how to retrieve the complete inventory, a
  selected ID, additional images, and search results. It does not assume the
  agent will discover command syntax elsewhere.

### I3.1. Image format and delivery

- Use a 1024 × 1024 PNG four-view sheet by default for import and search,
  with legible IDs, consistent view directions, a neutral background, and
  highlighted targets. Keep precise dimensions in the accompanying record.
- Include image attachments where the transport supports them, plus stable
  artifact references and retrieval actions in every execution mode. Offer
  higher-resolution or isolated views on request.
- Measure token use on supported agent models. Adjust resolution or image
  count through explicit budget options rather than claiming one universal
  image token cost.

### I3.2. Analysis completion and reuse

- By default, wait for the complete inspection report, including feature
  recognition and the selected default images, before returning the import
  response. A completed report states recognition coverage and technical
  limits; completion does not imply that every region has a known feature
  label.
- Support an explicit caller timeout and a background option for unusually
  large jobs. If the response is unfinished, distinguish successful geometry
  import from inspection progress, list pending or failed stages, and provide
  an executable wait/retrieval or resume action. State whether work is still
  running or has stopped. Pending recognition must not appear as zero features.
- Persist analysis for each geometry version and reuse it for inspection and
  search. The same reuse contract applies to imported and script-generated
  versions. Invalidate affected results when geometry, recognition software,
  configuration, or source evidence changes. Repeated queries on an unchanged
  version must not rerun full recognition.
- Establish latency expectations from complete import/report measurements,
  including validation, recognition, default image rendering, and artifact
  delivery. Recognition-only timings are not an end-to-end time guarantee.
- Define the supported input-size envelope and reference local hardware in
  milestone 1, including faces, occurrences, file size, and memory. Separate
  measured spike timings from projected complete-pipeline costs and identify
  unmeasured stages. Set numeric cold/warm gates for the first useful release
  before evaluating it; freeze full-report gates before milestone 4 evaluation.
  Measure the integrated pipeline as stages become available. A missed limit
  requires an explicit product decision about optimization, the supported
  envelope, or response budgets; it does not
  silently change the agreed default to partial/background delivery.

### I3.3. Context and image budgets

- Accept optional caller context-window size, total response budget, text
  token budget, image token budget, maximum inline image count, and a vision
  cost profile when known. Derive the approximate 5% target from supplied
  context size; explicit caller limits take precedence over that target.
- When context size is unknown, default to an estimated 2,000 text tokens and
  the agreed image caps: up to nine import samples or ten search images at
  1024 × 1024. State the applied fallback and estimation method. With no vision
  cost profile, enforce count/resolution caps and mark image token cost unknown;
  do not claim to have enforced 5% of an unknown context window.
- Reserve text first for baseline/revision identity, status and coverage,
  result counts, selected IDs, manifest references, and retrieval actions.
  Summarize or omit optional detail and excess records from the tail, stating
  returned counts and truncation. Keep the complete inventory retrievable.
- Allocate import images in deterministic priority order: overall view, then
  alternate ranked part and feature samples. Search images follow result rank.
  Apply the strictest supplied budgets and omit lower-priority images first;
  preserve their IDs and retrieval actions. If a hard combined or image token
  budget cannot be estimated without a vision profile, return text and image
  references with the reason images were omitted.
- Record delivered and omitted media and estimated text/image costs. Compare
  the agreed bundle against a smaller initial bundle on held-out agent tasks
  before release. Change the product defaults only through an explicit
  decision supported by target-selection outcomes.

### I3.4. Durable jobs, deadlines, and retries

- Bind each durable analysis job ID to one project, geometry version,
  analysis revision, requested stages, and configuration. Expose status,
  poll/wait, resume, and cancel actions through the shared CLI/MCP contract.
- A caller deadline ends that caller's wait; it does not implicitly cancel
  the worker. Explicit cancellation stops the requested work and preserves
  the committed baseline and completed artifacts. Report whether work is
  queued, running, stopped, failed, canceled, or complete.
- Accept an import idempotency key. Retrying the same key and semantic request
  after a lost response returns the same version/job; a changed caller wait
  deadline does not create a conflict. A conflicting import/analysis payload
  returns an error. A new key can intentionally import the same file again.
  Concurrent callers requesting the same version/revision analysis share
  in-flight work.
- Persist stage checkpoints and completed artifacts. After process or worker
  restart, resume the same durable job and version without duplicating the
  import or recomputing completed stages. Already published records remain
  retrievable through their immutable revision references.
- Exercise interruption, response loss, retries, concurrent callers, and
  cancellation on ordinary fixtures as well as large models. Build on the
  committed-core/artifact-status boundary and implement recovery for these
  operations without introducing a general-purpose scheduling product.

### I3.5. Retention and storage limits

- Apply configurable age and byte limits to derived search manifests, reports,
  and images. Choose documented defaults before their first release. Return
  retention state and expiry information with artifact/snapshot references;
  distinguish an expiry deadline from earlier eviction under byte pressure.
- Keep source/baseline geometry, the durable geometry manifest, and issued-ID
  memberships outside derived-cache eviction. Cleanup cannot permit ID reuse
  or retargeting. Expired payloads return an explicit unavailable/stale error
  and retrieval/restart action; regeneration never substitutes new evidence
  for the old immutable artifact.
- Active jobs protect the checkpoints and artifacts needed to finish or resume;
  these bytes count toward the quota. If space cannot be reclaimed safely,
  stop with an explicit resource-limit status and a resume action instead of
  deleting protected state or growing without bound. After the job stops,
  advertise which outputs remain protected and which are evictable.
- Protect an in-progress page/artifact read from concurrent cleanup. An idle
  cursor does not pin an entire snapshot indefinitely: retained pages remain
  immutable, while evicted snapshots fail explicitly on the next request.
  Use the existing local artifact store; no separate storage service is needed.

### I4. Editable starting point

- build123d is the reference editing backend for this effort and the first
  end-to-end release. ID-based face, edge, occurrence, and feature-member
  selection must work in its generated scaffold after normalized reload.
- Import supplies an `edit.py` that loads the finished imported solid or
  assembly as geometry and shows how to select an addressable ID. It does not
  claim to reconstruct the original parametric source program or feature tree.
- The baseline path and ID scope in the report match the geometry loaded by
  the scaffold. After a new version is created, the agent is told to inspect
  that version before reusing topology IDs.
- Inspection and search remain kernel-based; advertise editing support
  separately from analysis support. CadQuery's existing scaffold may continue
  to use native selectors, but the new typed-ID editing contract is initially
  supported only by build123d. Report unsupported backend/selector combinations
  with a runnable supported alternative. Preserve the configured runtime;
  never silently switch a CadQuery project to build123d.

### I5. Recognize named design features

- Recognize through and blind holes, counterbores and countersinks, slots,
  pockets, bosses, and fillets. Evaluate additional feature types and
  recognition approaches to deliver the strongest reliable inspection that
  available open-source technology supports within this effort's format and
  deployment scope. Milestone 1 probes representative recognition feasibility;
  the broader family assessment has its own fixed deadline and does not block
  the first import-to-edit release. Milestones 3.2 and 4 deliver feature
  families through the complete workflow.
- Compare available open-source geometric, connectivity-based, and learned
  recognition approaches against the same annotated imported models. Select
  methods using measured recognition quality, coverage, usefulness to an
  editing agent, latency, and resource cost. Record which approaches were
  evaluated and why the selected implementation provides the strongest
  demonstrated result.
- Build each recognized feature from measured geometry and its relationships.
  For example, a counterbored hole combines the bore, larger recess, and
  connecting surfaces into one inspectable feature.
- Each recognized feature has a typed, versioned ID; its member face and edge
  IDs; dimensions such as diameter and depth; orientation and extent; and
  highlighted views and retrieval actions. State the evidence for the label
  and distinguish imported source labels from inferred classifications.
- Expose recognized feature types and measurements in the default report,
  structured search filters, similarity ranking, and inspection records.
  Underlying faces and edges remain available for verification and selection.
- Report observable analysis coverage: families/configuration run, supported
  input cases, and analyzed, skipped, failed, and unclassified regions. Define
  each denominator and unit, such as analyzed faces out of the scoped face
  inventory; return unknown when a denominator cannot be established. Preserve
  measured groups when a design-feature label cannot be established reliably.
- Runtime analysis completion is not recognition recall. Analyzing every face
  does not establish that every feature was found, and an unlabeled face is
  not itself a missed feature. Reserve missed-feature counts and recall for
  annotated evaluation with ground truth. A zero-result feature query says no
  recognized matches and reports analysis coverage; it does not prove physical
  absence.
- Completion requires demonstrated recognition on real imported models,
  integration into the agent workflow, and documented technical limits.
  Recognition does not claim to recover the original source feature tree.
- Record a fixed duration/end date for the initial spike before it starts and
  a separate deadline for the broader required-family assessment. The latter
  produces a finite feature/input matrix, feature definitions, annotated
  development and held-out fixtures, per-type quality/membership gates, and
  local resource/license findings. Freeze each family's numeric gates before
  evaluating its implementation. Do not require full-family production
  evidence before the first useful release.
- Required feature types remain completion gates. If a type misses its gate,
  evaluate another open-source approach or improve the implementation. If the
  gate remains infeasible, bring the evidence to an explicit product scope
  decision. Documented limitations alone cannot silently waive a required
  family or turn the effort into an unbounded research project.

## Requirements: geometry search

### S1. Structured filters

- Search is scoped to a single project and an explicitly selected version.
  All parts in that version are eligible by default; an optional part filter
  narrows the candidate set. Search accepts composable filters for
  part, selected face or edge, spatial region, explicitly defined plane,
  entity kind, surface or curve type, orientation, axis, dimensions with units
  and tolerances, area, length, bounds, and placement.
- Default to occurrence-level results and assembly coordinates. Return
  `part_id` and `occurrence_id` with placed entities, and accept both as filters.
  An explicit definition-grouped result mode reports its grouping and distinct
  occurrence count. Echo the selected result mode and coordinate frame.
- Supported relationships include adjacency, shared or coaxial axis, distance,
  and explicit plane relations such as coplanar with or intersecting. A plane
  query states its origin/offset, normal, tolerance, and relation; the word
  `on` alone does not silently choose a relation.
- A hard filter is applied exactly as reported. Zero matches return zero
  matches, the applied query, coverage information, and useful refinement
  suggestions. Filters are never silently relaxed.
- Filtering is available for parts, topology entities, and candidate features
  where the required properties are known. Unsupported properties are reported
  explicitly rather than ignored. If filter evaluation is incomplete, return
  pending/partial coverage; do not label the candidate manifest or match count
  complete until every candidate has been accounted for.

### S2. Similarity to an example

- An agent can supply a reference part, face, or candidate-feature ID and
  request similar results from the same version. Filters can narrow the
  candidate set before similarity ranking.
- Part and feature similarity combine measured properties with actual shape
  comparison. Use dimensions, surface types, aspect ratios, and neighboring
  geometry to find candidates, then align candidates and compare their actual
  geometry to refine the ranking. Shape comparison is required, including
  cases where different shapes share the same measured properties.
- Explain the main matching and differing properties and the shape-comparison
  evidence for each ranked result. Evaluate candidate selection and final
  ranking together so early filtering does not hide the useful shape matches.
- Bound aligned geometry comparison by explicit attempted-comparison and
  elapsed-time budgets frozen before milestone 5 evaluation, including a
  per-comparison limit. Return eligible, evaluated, unscored, and failed counts.
  Evaluated means successfully shape-compared, failed means attempted but
  unsuccessful, and unscored means not attempted; these three counts sum to
  eligible count.
  Set `ranking_complete` only when no eligible candidates remain unscored or
  failed. A shortlist ranking is approximate relative to the full candidate
  set and must be labeled accordingly. Never represent unevaluated candidates
  as having received actual shape comparison.
- Pin deterministic candidate traversal, comparison configuration, and score
  tie-breaking. A wall-time stop reports partial coverage and its stop reason;
  different machines may compare different subsets before that deadline.
  Exact result parity applies to replay of a retained snapshot or comparison
  of the same successfully evaluated candidate set with the same geometry and
  configuration. Independent time-limited runs may have different subsets.
- Offer explicit exhaustive comparison through the same wait/background job
  contract when the caller needs ordering over every eligible candidate. A
  completed exhaustive ranking accounts for every candidate and discloses
  comparison failures; it cannot claim completeness over failed comparisons.
- The default comparison is sensitive to size and invariant to position and
  rotation. Alternative scale-invariant or location-sensitive behavior, if
  offered, must be an explicit query option.
- A similarity rank or score is relative to the stated reference and
  candidate set. It is not presented as a universal percentage of sameness or
  as proof that two features have the same function.
- If an imported model has no other eligible candidate, return an empty result
  with that reason. One part occurrence has no other part occurrence to match,
  but can still contain similar faces or features. Repeated instances of one
  definition are eligible occurrence matches; grouping by definition collapses
  them and reports the resulting distinct-definition count.

### S3. Result delivery and inspection

- Each search response echoes the baseline, applied filters, similarity
  reference and comparison options, match count, result count, and limitations.
  Include a search snapshot ID, analysis/index revisions, ranking mode,
  coverage counts, and the applied text/image/work budgets.
- Return the first 10 results by default, each with ID, parent part, hard
  dimensions, location, rank evidence and code selector when applicable, and
  a path to a highlighted four-view image.
- Include up to 10 first-page images in the agent response by default, subject
  to explicit image-budget controls. Deduplicate identical views. The agent
  can request fewer results, fewer images, or no images.
- Save the complete hard-filter candidate manifest as an artifact and return
  its reference. Distinguish this inventory from the shape-ranked result set:
  include ranking/evaluation state for every similarity candidate. For
  similarity queries, the default first page contains successfully compared
  candidates ordered by the stated ranking method.
  Unscored candidates remain discoverable through the manifest and exhaustive
  continuation, without fabricated scores. Large manifests remain references.
- Exact-filter queries without a similarity reference perform zero shape
  comparisons. Return matching records in deterministic public-ID order with
  ordinary match counts and pagination. Set ranking mode to `not_applicable`
  and comparison counts/`ranking_complete` to null; they do not describe this
  query. Report filter coverage separately, including pending/partial status.
- Bind pagination to an immutable query/geometry/analysis/index/ranking
  snapshot with deterministic tie-breaking by public ID. Completing more
  comparisons or re-analyzing geometry creates a new snapshot; it does not
  reorder an existing cursor. Expired snapshots return an explicit error and
  restart action. Retrieve inspection and images by pinned identity/revision
  while retained; expired payloads follow I3.5 rather than substituting a
  newer report or image.
- Grouped results expose member topology IDs. Highlighting, measurements,
  search records, and edit selectors refer to the same baseline entities.

### S4. CLI, MCP, and execution-mode parity

- CLI and MCP accept the same query fields and produce the same structured
  result fields, ID scopes, artifact references, and error meanings. Exact
  queries and retained snapshot replays have equivalent counts and ordering.
  Fresh similarity runs follow S2's evaluated-set parity rule; wall-time stops
  may produce different subsets and must disclose partial coverage.
- Image transport may differ: the CLI returns local paths or retrievable
  artifact references; MCP may also attach images. Both allow the agent to
  retrieve the same images and full manifests.
- Direct ID lookup, filtered search, similarity search, pagination, and
  inspect-result retrieval are available through both interfaces.
- All requirements apply to local, cloud, and remote mode. An artifact
  reference includes a stable artifact ID and an executable retrieval action;
  a worker's filesystem path alone is insufficient for a remote caller.
  Local paths, downloadable URLs, and image attachments are transport options
  for the same artifacts. Expiring download links can be refreshed by artifact
  ID without changing geometry IDs or search ordering.

## Illustrative query contract

The final command and tool names are implementation choices. The shared
machine-readable request should support the following shape:

```json
{
  "baseline": "v1_cadgenbench_101_agentcad/output.step",
  "result_scope": "occurrences",
  "coordinate_frame": "assembly",
  "filters": {
    "part_id": "part_v1_1",
    "kind": "cylindrical_face",
    "diameter_mm": {"value": 12, "tolerance": 0.1},
    "axis": "Z"
  },
  "sort": {"by": "similarity", "reference_id": "face_v1_55"},
  "ranking": {"mode": "bounded", "max_shape_comparisons": 100},
  "limit": 10,
  "budget": {"text_tokens": 2000, "max_images": 10}
}
```

The comparison count above is an illustrative caller limit, not a fixed
product default. Freeze default work budgets from measurements before
milestone 5 evaluation.
The response pins a search snapshot and returns `eligible_count`,
`evaluated_count`, `unscored_count`, `failed_count`, and `ranking_complete` so
the caller can distinguish a complete inventory from exhaustive shape ranking.

The result must distinguish matching faces from distinct cylinder-axis
locations. In the CADGenBench 101 concept fixture, the Ø12 query matches 11
faces at 10 axis locations; an agent must not mistake those counts for 11
separate holes. F50, F52, F54, and F55 are example version-local face IDs that
can be shown together or inspected separately.

## Non-goals for this PRD

- Built-in natural-language query interpretation. The calling agent may
  translate user intent into structured query fields. This exclusion does
  not preclude learned recognition that meets the open-source dependency
  requirements for milestone I5.
- Reconstruction of a STEP or BREP file into editable parametric feature-tree
  source code. Editing begins by loading the imported finished geometry.
- Guaranteed persistent face or feature identity across edited versions.
  Best-effort correspondence is required and must expose ambiguity.
- Automatic proof that an edit changed only the intended region. Expected
  change and source-preservation checks belong to Specify and Validate.
- A mesh-specific import and search experience for STL, OBJ, or 3MF. Those
  need component and region semantics suited to tessellated geometry.
- A 2D drawing/sketch inspection experience. Preserve it as a future product
  idea, including links from drawing dimensions to 3D geometry where possible.
- Search across projects or a library of projects. Searching across parts
  within the selected project version is in scope.
- Interactive search controls in the human-facing viewer. The viewer may
  display the report and highlights; the new query interface is for agents.
- Proprietary recognition SDKs and paid inference services in this effort.
- Typed-ID editing helpers for CadQuery. build123d is the supported backend
  for the new editable selection contract; analysis support is reported
  independently, and existing CadQuery workflows retain their runtime.

## Future ideas

- Compare any object ID with any other object ID, including objects from
  different versions or unrelated regions. Return meaningful dimension,
  geometry, and placement differences plus visual comparisons where supported.
  An arbitrary comparison must not imply that the objects share lineage.
  This extends the shared ID contract; it is outside this release's scope.
- Evaluate additional recognition engines and services for a future paid cloud
  offering. That decision does not change this effort's open-source local
  implementation or its dependency requirements.

## Implementation boundaries

- Use open-source recognition, geometry analysis, and similarity components in
  every deployment mode for this effort. Dependencies must have licenses
  compatible with AgentCAD's distribution and intended use. Any bundled or
  required model assets must also permit that use; source-available or
  noncommercial-only research releases do not satisfy this requirement.
  After installing the required open-source packages and model assets, the
  local implementation must run without a proprietary SDK, license server,
  or paid hosted inference endpoint.
- Build a versioned, rebuildable search index from normalized geometry. The
  storage format is an engineering choice; a per-version JSON index searched
  in memory is sufficient to begin. Measure larger assemblies before moving
  to SQLite or another indexed store. The index is derived data and records
  the normalized geometry hash and index schema version.
- Persist the durable geometry/ID manifest independently of this rebuildable
  index. Share intrinsic measurements and recognition for repeated definitions;
  retain each occurrence's placement and public identity separately.
- Compute intrinsic measurements once, store topological adjacency sparsely,
  and narrow candidates before expensive distance, coaxial, or spatial checks.
  Do not materialize an all-pairs relationship table. Keep query semantics and
  CLI/MCP responses independent of JSON, SQLite, or any future storage engine.
- Benchmark index construction and warm queries on a repeated-component
  assembly and a high-face-count part in the first useful release. Enforce
  the peak-memory, build-time, and warm-query limits established in milestone
  1; changing databases alone is not evidence that geometry work is bounded.
- Extract measurements, part membership, and relationships through one shared
  indexing path. Search must not create an independent ID mapping from the
  one used by inspection and edit selectors.
- Render the default samples during import; generate other images on request
  and cache them for reuse. Record render failures as
  partial artifact status without hiding the underlying geometry record.
- Keep source assembly metadata alongside normalized geometry because STEP
  re-export may not preserve all supplier names and hierarchy.

## Acceptance and evaluation

### Evaluation setup

- Milestone 1 creates a versioned gate registry, measurement methods, reference
  hardware/input envelope, and numeric gates needed for milestone 2. Add and
  freeze each later milestone's numeric thresholds, fixtures, and relevant
  configuration before evaluating that implementation. Milestones cannot
  pass with unset thresholds or thresholds selected after held-out evaluation.
  Full-report production measurements and every later family/ranking gate do
  not block the first import-to-edit release.
- Include per-feature precision/recall, dimensional tolerances, face/edge
  membership accuracy, candidate recall, top-10 similarity relevance, peak
  memory, index-build time, cold/warm report latency, warm query latency, and
  ranking work limits. Evaluate feature families separately.
- Use simple diagnostic models and real imported parts/assemblies. Include
  nested repeated components with rotated instances, a high-face-count part,
  split analytic surfaces, compound features, and valid-looking near misses.
  Split annotated development fixtures from held-out models and targeting
  tasks; record provenance and permitted fixture use.
- Before changing the workflow, record a baseline using today's
  import/inspect/measure/render tools. Pre-register expected target geometry
  and task behavior. Compare matched agent models, prompts, and execution
  conditions, measuring time to first correct editable selection, tool calls,
  text/image context consumed, and wrong-target rate. An independent checker
  verifies the selected geometry and resulting edit.
- Include neutral editing tasks and a target omitted from the default sample
  images so retrieval is exercised. Compare the agreed image bundle with a
  smaller bundle; report outcomes rather than optimizing image count alone.
  Run the relevant workflow/value checks from the first useful release and
  repeat the held-out comparison at the full release gate.
- Record a finite effort/time box for the feasibility assessment. If a
  mandatory recognition type or the complete-report default cannot meet its
  predeclared quality/resource gate, require an explicit product decision
  supported by measurements before proceeding with a changed contract.

### Required acceptance cases

1. For a valid one-part STEP fixture, import returns quality and provenance,
   whole-model dimensions, the overall four-view, a complete accessible
   inventory, representative feature samples, and an `edit.py` that loads the
   same baseline. The identical part image is deduplicated in context.
2. For a multi-part STEP with named components, the report lists individual
   parts and source names when present. Missing names are explicitly marked;
   the geometry is not misleadingly reported as one supplier part.
   A nested assembly with two rotated instances of one definition retains
   distinct occurrence IDs, paths, transforms, and selectable members after
   reload. Counts distinguish definitions from occurrences; local and assembly
   coordinate queries return the expected instances. Intrinsic analysis can
   be shared without sharing occurrence identity.
3. For sampled face and edge IDs, the inspection record, highlighted image,
   search result, and code selector identify the same geometry. This is
   checked after normalized STEP reload, not only in the initial parser shape.
   IDs include type and version; unambiguous version correspondences retain
   their entity numbers, while splits, merges, and ambiguous matches expose
   new numbers. Using an ID against the wrong baseline returns an error.
4. Exact filter queries return the expected set and count under stated
   tolerances. Plane and relationship queries state the applied relation.
   A query without a part filter can return matches from multiple parts in
   the selected project version; a part filter narrows those results. No query
   retrieves candidates from another project.
5. Similarity tests include translated and rotated copies, different sizes,
   near matches, unrelated geometry, and different shapes with matching
   dimensions or volume. Both measured properties and aligned geometry
   comparison contribute to the ranking. Ranking explanations identify the
   measured similarities and actual shape differences. Freeze a top-10
   relevance target and candidate-recall gate before milestone 5 evaluation.
   Bounded ranking reports eligible/evaluated/unscored/failed counts, discloses
   approximation, and stays within its work budget. Exhaustive mode accounts
   for every candidate and comparison failure.
6. With sufficient matches and response budget, exact queries return ten
   records and images by default, plus the complete candidate manifest and a
   working next page. A fixture with more than ten exact matches and no
   similarity reference retrieves all pages with zero shape comparisons and
   ranking fields marked not applicable. Similarity queries use successfully
   compared candidates for their ranked pages. Lower budgets report omissions.
   Pagination is deterministic and pinned to a snapshot, including while a
   newer analysis or exhaustive ranking becomes available. Image-free mode
   returns the same matches when replaying that snapshot; separate time-limited
   executions may have different evaluated sets.
7. Equivalent CLI and MCP exact requests and retained snapshot replays return
   equivalent IDs, measurements, counts, ordering, and artifacts. Fresh
   similarity runs have exact parity for the same evaluated candidate set and
   configuration. Inject a slow comparison to prove a wall-time stop discloses
   partial coverage rather than promising the same subset across machines.
   Neither route silently drops an unsupported filter.
   The build123d scaffold imports, looks up an ID, selects the intended entity
   or occurrence, and saves an edited version. CadQuery reports the unsupported
   typed-ID editing combination without silently changing the runtime.
8. Record import time, search latency, index and image sizes, and measured
   model-context cost on small parts and large assemblies. Tune image sizes
   and budgets against this evidence before release.
9. Run the same import, image retrieval, filtered search, and similarity
   requests locally and through cloud/remote execution. Every returned artifact
   is accessible to the caller without access to the worker's local filesystem.
10. Validate recognized feature types on annotated real imports, including
    split surfaces, intersecting or combined features, and similar-looking
    regions that do not satisfy the feature definition. Measure false
    classifications, missed features, dimensional accuracy, and completeness
    of face/edge membership separately for each type. Set per-type acceptance
    targets before evaluating the implementation; document unsupported cases
    and the technical evidence for those limits.
    On an unfamiliar model, runtime coverage uses stated observable denominators
    and unknown values; benchmark recall is reported separately. A fixture with
    a real feature missed by the recognizer returns zero recognized matches
    after completed analysis without claiming the feature is absent.
11. In an agent workflow, a recognized feature can be identified in the default
    report, retrieved by its ID, filtered and ranked in search, rendered with
    every member highlighted, and selected in the edit scaffold. The complete
    effort must meet this criterion; finishing geometric grouping alone is an
    intermediate milestone.
12. The default import waits for a finished report with the selected images
    and recognition coverage. Explicit timeout/background responses identify
    unfinished work and provide a working continuation. Repeated inspection
    or search of unchanged geometry reuses the recorded recognition results.
    Interrupt recognition, restart the process/worker, and resume the same
    job/version with completed artifacts still retrievable. A lost response
    and concurrent retries of the same request do not create extra versions
    or duplicate analysis. Caller timeout and explicit cancellation have the
    documented distinct effects.
13. The full local inspection workflow, including named-feature recognition
    and similarity, runs using only the required open-source components and
    model assets. Verify dependency and model licenses and demonstrate local
    execution without proprietary SDKs, license servers, or paid services.
14. Change recognizer/configuration on unchanged STEP bytes. Any changed
    feature membership receives a new ID; old reports, feature selectors,
    images, and search cursors retain their original revision and membership
    or return an explicit unavailable/stale response. No old ID retargets.
15. On held-out targeting tasks, meet the predeclared time-to-correct-selection
    and wrong-target gates against today's workflow. Include retrieval of an
    unsampled target and a repeated occurrence. Record tool calls and context
    consumed as supporting measurements. These evaluation checks do not add
    automatic edit-preservation enforcement to the product scope.
16. Issue many queries/images under a small age/byte quota. Cleanup keeps
    retained snapshots consistently pageable and makes expired cursors and
    artifacts return the documented error/restart action. Protect active reads
    and required job checkpoints, and stop with a resource-limit status if
    protected bytes prevent progress. Durable geometry and issued IDs survive;
    no number is reused or reference silently retargeted.

## Delivery milestones

Each milestone has its own entry criteria, visible demonstration, fixtures,
pass/fail gates, and release decision. Apply relevant regression and agent
workflow checks when each slice lands. The gate registry starts in milestone 1;
each slice's performance and quality thresholds are frozen before evaluation.

Intermediate releases advertise their supported stages and feature families.
Before recognition ships, report `recognition_not_available`, not an empty
feature inventory or indefinitely running recognition. Reports are complete
for their advertised capabilities; the effort is complete only when every
required family and the final workflow gates pass.

### Milestone 1. Feasibility, contracts, and evaluation baseline

- **Entry:** agreed product scope, open-source constraint, and build123d
  editing backend.
- **Demonstration:** a spike with a fixed duration/end date recorded before
  work begins. Probe source assembly/occurrence mapping, ID selection after
  normalized reload in build123d, and one representative recognition family
  with member evidence and local resource/license findings. Record today's
  workflow baseline and outline revision/job/budget/search contracts.
- **Fixtures:** a real one-part model, nested assembly with rotated repeats,
  representative annotated recognition examples, and a high-face-count model.
  Reserve held-out tasks and plan the broader required-family fixture matrix.
- **Pass/fail:** milestone 2 is blocked only by unresolved baseline/occurrence/
  reload-selection feasibility, incompatible required dependencies, or missing
  milestone 2 contracts and numeric resource/agent-value gates. Label measured
  spike results separately from projected full-pipeline costs and unmeasured
  stages. Production-equivalent full reports, all-family recognition quality,
  and final similarity budgets are not exit requirements.
- **Release decision:** at the fixed deadline, proceed to milestone 2 when its
  blocking evidence passes, or resolve the specific blocker explicitly. The
  broader family assessment continues against its own recorded deadline and
  freezes per-family gates before evaluation. It does not delay the first
  slice; named-feature completion remains mandatory.

### Milestone 2. First useful import-to-edit release

- **Entry:** milestone 1 blocking evidence passes and milestone 2 contracts
  and numeric gates are frozen.
- **Demonstration:** import a representative STEP, receive quality/dimensions
  and an overall labeled view, retrieve a face/edge by ID, and select and edit
  it through the build123d scaffold after normalized reload. Establish the
  shared response schema, thin CLI/MCP adapters, lazy image retrieval, durable
  baseline/ID manifest, and one remote artifact round trip.
- **Fixtures:** a one-part real model, named nested assembly with two rotated
  instances, and the high-face-count/repeated-component performance fixtures.
- **Pass/fail:** image/record/selector agreement and intended-occurrence edits
  pass all fixtures; runtime/capability reporting is accurate. Prove bounded
  text/media delivery, committed-core preservation, idempotent import retry,
  and caller-accessible remote artifacts. Meet milestone 1 memory, index-build,
  and warm-query gates. Measure initial agent value, including an unsampled ID.
- **Release decision:** ship the useful geometry workflow with explicit
  recognition capability status. Richer samples and recognition extend this
  verified contract.

### Milestone 3. Exact filters and one named feature family

Milestones 3.1 and 3.2 are separately releasable; both are required to finish
milestone 3. Recognition quality and recovery do not block raw-geometry filters.

#### Milestone 3.1. Raw-geometry filters and pagination

- **Entry:** milestone 2 passes; exact-filter and retention gates are frozen.
- **Demonstration:** filter parts, occurrences, faces, and edges using measured
  properties and relationships. Return complete candidate manifests and pinned
  pagination across CLI/MCP and remote execution without shape comparison.
- **Fixtures:** expected exact-match sets, more than ten matches, repeated
  instances, and small-quota retention/performance cases.
- **Pass/fail:** expected sets/counts and all pages pass, ranking fields are
  not applicable, and shape-comparison calls stay at zero. Verify parity,
  remote retrieval, quota cleanup, stable retained pages, and explicit expiry.
  Meet applicable resource and agent-targeting gates.
- **Release decision:** ship exact filters independently with accurate
  recognition capability status, even if milestone 3.2 is still in progress.

#### Milestone 3.2. First recognized family through the workflow

- **Entry:** milestone 3.1 passes; choose the first family from the broader
  assessment and freeze its numeric quality/dimension/membership gates.
- **Demonstration:** recognize that family through report, ID lookup, exact
  filter, highlighted members, and build123d edit selection using the existing
  search path. Expose runtime coverage separately from benchmark recall.
- **Fixtures:** annotated positives, near misses, split surfaces, repeated
  instances, and a real feature missed despite completed analysis.
- **Pass/fail:** meet family gates and workflow checks. Prove recognition
  interruption/restart/resume, concurrency, cancellation, lost-response retry,
  and immutable membership after configuration changes. A zero recognized
  result cannot claim physical absence. Meet resource and agent-value gates.
- **Release decision:** ship the validated family with explicit coverage and
  finish milestone 3; remaining required families continue in milestone 4.

### Milestone 4. Complete required recognition and report

- **Entry:** milestone 3.2 passes; remaining family and full-report numeric
  gates are frozen before evaluation.
- **Demonstration:** deliver remaining required families in measured batches,
  preserving raw topology and conservative groups. Connect each family to
  reports, retrieval, filters, images, and editable member selection. Deliver
  the complete-report wait behavior and full agreed sample bundle.
- **Fixtures:** every required family, compound/intersecting features, ambiguous
  regions, repeated definitions, and realistic small/large imports.
- **Pass/fail:** every required family meets its predeclared gates; unknown
  regions and coverage are accurate. Preserve old feature references across
  recognizer upgrades and membership changes. Meet cold/warm report, memory,
  context, and workflow gates. A missed required family triggers the stated
  feasibility/scope decision, not an automatic completion exception.
- **Release decision:** ship complete recognized-feature inspection for the
  supported input matrix. Part similarity work may proceed independently;
  generalized feature similarity builds on these stable records.

### Milestone 5. Measured and shape-based similarity

- **Entry:** stable recognized-feature records and exact queries from
  milestone 4, with similarity quality/resource/work-budget gates frozen;
  part definitions and occurrences are already established.
- **Demonstration:** use a part, face, or feature as an example, narrow by
  filters, and retrieve explained results using measured properties followed
  by aligned shape comparison. Expose bounded ranking and explicit exhaustive
  continuation, complete candidate manifests, and immutable result pages.
- **Fixtures:** rotated/translated counterparts, size differences, repeated
  instances, near matches, and different shapes sharing measured dimensions.
- **Pass/fail:** meet predeclared candidate-recall and top-10 relevance gates
  within resource budgets. Coverage counts reconcile; approximation and failed
  comparisons are explicit; old cursors never reorder when analysis or ranking
  progresses. Prove snapshot/evaluated-set parity, explicit partial coverage
  under injected slow comparisons, and remote result/image retrieval.
- **Release decision:** ship similarity with its measured coverage and bounded
  work contract.

### Milestone 6. Full regression and release gate

- **Entry:** all preceding milestone gates pass.
- **Demonstration:** import an unfamiliar real model, identify the intended
  feature or occurrence, retrieve an unsampled target, search, and save a
  correctly targeted build123d edit using the returned identities.
- **Fixtures:** held-out parts, nested assemblies, required feature families,
  and small/large performance cases across local and cloud/remote execution.
- **Pass/fail:** all cross-cutting acceptance cases pass; every required family
  meets its quality gates; end-to-end performance and agent-value targets pass
  against the recorded baseline. Confirm response budgets, open-source local
  execution, recovery, snapshots, and the supported-backend capability matrix.
- **Release decision:** declare the effort complete with demonstrated
  recognition coverage and technical limits. Record any measured default
  changes as explicit product decisions before the final release.
