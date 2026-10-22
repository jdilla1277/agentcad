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

This effort targets STEP/STP and BREP solid geometry supported by the
existing import path. A **part** is a source assembly component when source
structure is available, or an explicitly labeled derived solid when it is not.
A **topology entity** is an addressable solid, face, or edge. A **candidate
feature** is a geometric grouping inferred from one or more entities, such as a
cylindrical region; it is not a recovered parametric feature-tree operation.
A **recognized feature** assigns a supported design-feature type to a measured
region, such as a counterbored hole, with its dimensions, member entities,
and recognition evidence.

Part and topology IDs are scoped to the normalized geometry of one version.
Their version component changes after an edit; the entity number is retained
when correspondence is unambiguous. Candidate-feature groups also name their
member topology IDs. Cross-version correspondence is best effort, and temporary
search-result groups have no continuity guarantee.

The source file is retained for provenance. Measurements, IDs, renders, search
results, and `edit.py` selectors must derive from the same normalized version
geometry that the edit script loads.

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
- Keep measured geometric facts distinct from inferred design purpose. A
  cylindrical surface is not automatically a hole.

### I2. Geometry inventory and identity

- Provide a complete machine-readable part list and complete indexed inventory
  of addressable solids, faces, and edges for the imported version.
- Each record includes the parent part, kind, relevant hard dimensions,
  orientation, location or bounding box, measurement units, and an inspection
  and image retrieval path. Include source name and provenance where available.
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

### I2a. Object IDs and continuity between versions

- Public IDs encode object type, version, and an entity number: `part_v1_1`,
  `face_v1_55`, `edge_v1_17`, and `feature_v1_3`. Compact aliases such as `F55`
  may appear in images when the version is also labeled. Records include the
  canonical version identifier and baseline hash to disambiguate versions
  from different projects.
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
  Large inventories and non-sample images are references to artifacts, not
  injected payloads. The agent can request an expanded or image-free response.
- The default response explains how to retrieve the complete inventory, a
  selected ID, additional images, and search results. It does not assume the
  agent will discover command syntax elsewhere.

### I3a. Image format and delivery

- Use a 1024 × 1024 PNG four-view sheet by default for import and search,
  with legible IDs, consistent view directions, a neutral background, and
  highlighted targets. Keep precise dimensions in the accompanying record.
- Include image attachments where the transport supports them, plus stable
  artifact references and retrieval actions in every execution mode. Offer
  higher-resolution or isolated views on request.
- Measure token use on supported agent models. Adjust resolution or image
  count through explicit budget options rather than claiming one universal
  image token cost.

### I3b. Analysis completion and reuse

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

### I4. Editable starting point

- Import supplies an `edit.py` that loads the finished imported solid or
  assembly as geometry and shows how to select an addressable ID. It does not
  claim to reconstruct the original parametric source program or feature tree.
- The baseline path and ID scope in the report match the geometry loaded by
  the scaffold. After a new version is created, the agent is told to inspect
  that version before reusing topology IDs.

### I5. Recognize named design features

- Recognize through and blind holes, counterbores and countersinks, slots,
  pockets, bosses, and fillets. Evaluate additional feature types and
  recognition approaches to deliver the strongest reliable inspection that
  available open-source technology supports within this effort's format and
  deployment scope. Begin the milestone by assessing available capabilities
  and methods beyond conservative grouping.
- Compare available open-source geometric, connectivity-based, and learned
  recognition approaches against the same annotated imported models. Select
  methods using measured recognition quality, coverage, usefulness to an
  editing agent, latency, and resource cost. Record which approaches were evaluated and
  why the selected implementation provides the strongest demonstrated result.
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
- Report recognition coverage and uncertainty. Preserve measured geometric
  groups when a design-feature label cannot be established reliably. Track
  missed and unrecognized regions explicitly; an unknown label does not imply
  the absence of a feature.
- Completion requires demonstrated recognition on real imported models,
  integration into the agent workflow, and documented technical limits.
  Recognition does not claim to recover the original source feature tree.

## Requirements: geometry search

### S1. Structured filters

- Search is scoped to a single project and an explicitly selected version.
  All parts in that version are eligible by default; an optional part filter
  narrows the candidate set. Search accepts composable filters for
  part, selected face or edge, spatial region, explicitly defined plane,
  entity kind, surface or curve type, orientation, axis, dimensions with units
  and tolerances, area, length, bounds, and placement.
- Supported relationships include adjacency, shared or coaxial axis, distance,
  and explicit plane relations such as coplanar with or intersecting. A plane
  query states its origin/offset, normal, tolerance, and relation; the word
  `on` alone does not silently choose a relation.
- A hard filter is applied exactly as reported. Zero matches return zero
  matches, the applied query, coverage information, and useful refinement
  suggestions. Filters are never silently relaxed.
- Filtering is available for parts, topology entities, and candidate features
  where the required properties are known. Unsupported properties are reported
  explicitly rather than ignored.

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
- The default comparison is sensitive to size and invariant to position and
  rotation. Alternative scale-invariant or location-sensitive behavior, if
  offered, must be an explicit query option.
- A similarity rank or score is relative to the stated reference and
  candidate set. It is not presented as a universal percentage of sameness or
  as proof that two features have the same function.
- If an imported model has no other eligible candidate, return an empty result
  with that reason. A single-part model cannot produce similar *parts* within
  that model, but can still return similar faces or feature candidates.

### S3. Result delivery and inspection

- Each search response echoes the baseline, applied filters, similarity
  reference and comparison options, match count, result count, and limitations.
- Return the first 10 results by default, each with ID, parent part, hard
  dimensions, location, rank evidence, code selector when applicable, and a
  path to a highlighted four-view image.
- Include up to 10 first-page images in the agent response by default, subject
  to explicit image-budget controls. Deduplicate identical views. The agent
  can request fewer results, fewer images, or no images.
- Save the complete matching-result manifest as an artifact and return its
  path. Additional result pages and any result's full inspection and four-view
  image are retrievable by ID. Large manifests are not injected into context.
- Grouped results expose member topology IDs. Highlighting, measurements,
  search records, and edit selectors refer to the same baseline entities.

### S4. CLI, MCP, and execution-mode parity

- CLI and MCP accept the same query fields and produce the same structured
  result fields, counts, ID scopes, artifact paths, and error meanings.
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
  "filters": {
    "part_id": "part_v1_1",
    "kind": "cylindrical_face",
    "diameter_mm": {"value": 12, "tolerance": 0.1},
    "axis": "Z"
  },
  "sort": {"by": "similarity", "reference_id": "face_v1_55"},
  "limit": 10,
  "images": 10
}
```

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
- Extract measurements, part membership, and relationships through one shared
  indexing path. Search must not create an independent ID mapping from the
  one used by inspection and edit selectors.
- Render the default samples during import; generate other images on request
  and cache them for reuse. Record render failures as
  partial artifact status without hiding the underlying geometry record.
- Keep source assembly metadata alongside normalized geometry because STEP
  re-export may not preserve all supplier names and hierarchy.

## Acceptance and evaluation

1. For a valid one-part STEP fixture, import returns quality and provenance,
   whole-model dimensions, the overall four-view, a complete accessible
   inventory, representative feature samples, and an `edit.py` that loads the
   same baseline. The identical part image is deduplicated in context.
2. For a multi-part STEP with named components, the report lists individual
   parts and source names when present. Missing names are explicitly marked;
   the geometry is not misleadingly reported as one supplier part.
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
   measured similarities and actual shape differences. Establish a top-10
   relevance target from a curated fixture set before release.
6. A search with more than 10 matches returns 10 records and images by
   default, plus the complete manifest and a working way to retrieve the next
   page. Image-free mode returns the same geometry matches.
7. Equivalent CLI and MCP requests return equivalent IDs, measurements,
   counts, ordering, and artifact references. Neither route silently drops an
   unsupported filter.
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
11. In an agent workflow, a recognized feature can be identified in the default
    report, retrieved by its ID, filtered and ranked in search, rendered with
    every member highlighted, and selected in the edit scaffold. The complete
    effort must meet this criterion; finishing geometric grouping alone is an
    intermediate milestone.
12. The default import waits for a finished report with the selected images
    and recognition coverage. Explicit timeout/background responses identify
    unfinished work and provide a working continuation. Repeated inspection
    or search of unchanged geometry reuses the recorded recognition results.
13. The full local inspection workflow, including named-feature recognition
    and similarity, runs using only the required open-source components and
    model assets. Verify dependency and model licenses and demonstrate local
    execution without proprietary SDKs, license servers, or paid services.

## Delivery milestones

1. Preserve source part metadata and create the shared per-version geometry
   index and ID-to-image mapping.
2. Deliver the default import report, media bundle, retrieval calls, and
   editable scaffold contract.
3. Add exact structured filters, relationships, complete result manifests,
   and CLI/MCP parity across local, cloud, and remote execution.
4. Add part and feature similarity ranking with query-specific evidence.
   Deliver both measured-property retrieval and aligned shape comparison.
5. Deliver named design-feature recognition: assess available open-source
   technology, implement the strongest validated approach, and integrate
   recognized features into reports, IDs, images, search, and edit selection. Geometric
   grouping establishes the foundation for this required milestone.
6. Run all acceptance fixtures and context/latency evaluation; refine sample
   selection, ranking, and budgets from observed agent behavior.

Earlier milestones may ship incrementally. This effort is complete after the
named-feature recognition milestone and the full agent workflow evaluation
pass, with demonstrated coverage and documented technology limits for the
supported input formats.
