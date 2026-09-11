# Product plan: Make CadQuery an optional compatibility extra

**Status:** In progress. Package, runtime, CI, and CLI/README documentation
changes shipped in agentcad 0.6.0 (this repo). The customer-facing website
page is tracked in the internal repo alongside the site.

**Scope:** Packaging and runtime separation

**Recommended extra name:** `cadquery`

## Summary

Make build123d the only CAD authoring runtime installed by default. Move CadQuery
behind an explicit `agentcad[cadquery]` extra while preserving the existing
CadQuery compatibility path for users who opt into it.

After this change:

- `pip install agentcad` installs the build123d/OCP runtime and does not install
  CadQuery, CasADi, or CasADi's bundled COIN-OR/METIS solver stack.
- `pip install "agentcad[cadquery]"` retains legacy CadQuery script support and
  its current dependency chain.
- Attempting to run a CadQuery script without the extra returns a structured,
  actionable error instead of an import traceback.

## Problem

AgentCAD defaults to build123d, but `cadquery>=2.0` is still a mandatory package
dependency. CadQuery unconditionally depends on CasADi, whose binary wheels
include numerical solver libraries such as Ipopt, MUMPS, and METIS. This creates
commercial-license review work for every AgentCAD installation even when the
user never selects the CadQuery compatibility runtime.

The mandatory CadQuery dependency also increases installation size, resolution
complexity, and native-library surface area for the primary build123d workflow.

## Goals

1. Give the default PyPI installation a build123d-only dependency graph.
2. Remove CasADi and its bundled COIN-OR/METIS libraries from fresh default
   installations.
3. Preserve CadQuery compatibility through an explicit optional extra.
4. Keep all existing build123d commands, output formats, validation, rendering,
   inspection, diffing, and MCP behavior working without CadQuery installed.
5. Clearly explain how legacy CadQuery users enable compatibility.

## Non-goals

- Removing or replacing OpenCascade/OCP. build123d still requires OCP.
- Reimplementing CadQuery's authoring API.
- Changing AgentCAD's Apache-2.0 license.
- Certifying the optional CadQuery dependency chain for commercial use.
- Removing CadQuery compatibility entirely in this phase.

## User experience

### Default installation

```console
pip install agentcad
agentcad init --name model
agentcad run model.py --label first
```

This supports build123d only and does not install `cadquery` or `casadi`.

### CadQuery compatibility installation

```console
pip install "agentcad[cadquery]"
agentcad init --name legacy-model --runtime cadquery
```

### Missing-extra behavior

If runtime detection, `--runtime cadquery`, or a project manifest selects
CadQuery when the extra is absent, AgentCAD must return its normal structured
error response. The message should say:

> CadQuery compatibility is not installed. Install it with
> `pip install "agentcad[cadquery]"` in the same environment as agentcad,
> then run `agentcad daemon restart` if a daemon is running.

No raw `ModuleNotFoundError` or partial command output should escape.

## Requirements

### Packaging

- Remove `cadquery>=2.0` from the default dependency list.
- Remove the default Windows `casadi<3.8` constraint.
- Add a `cadquery` optional extra containing `cadquery>=2.0` and the applicable
  Windows CasADi constraint.
- Consider declaring the compatible `cadquery-ocp` range directly because
  AgentCAD imports OCP APIs itself; do not rely solely on a transitive dependency
  if AgentCAD owns that API relationship.
- Ensure CI's complete test environment explicitly installs
  `.[dev,mcp,cadquery]`.

Illustrative metadata—not final version constraints:

```toml
dependencies = [
    "click>=8.0",
    "build123d>=0.10,<0.11",
    "ocp_gordon<0.3",
]

[project.optional-dependencies]
cadquery = [
    "cadquery>=2.0",
    "casadi<3.8; sys_platform == 'win32'",
]
```

### Runtime separation

- Runtime detection and help commands must not import CadQuery.
- The CadQuery runner may import CadQuery lazily only after the runtime has been
  selected and dependency availability has been checked.
- Daemon startup must not unconditionally import or warm CadQuery. It may warm
  CadQuery when installed, but a missing extra must never prevent build123d
  daemon startup.
- Daemon status/environment information should report whether CadQuery
  compatibility is available.

### Kernel-neutral shared operations

Remove CadQuery from shared build123d workflows:

- Replace the CadQuery-based STEP loader in `agentcad.step_io` with build123d or
  direct OCP STEP reading.
- Remove the redundant CadQuery import in the render command.
- Keep common metrics, validation, inspection, render, diff, and mesh export on
  raw `TopoDS_Shape`/OCP or build123d APIs.
- Keep CadQuery-only wrapping and export behavior contained in the optional
  CadQuery runner and explicitly CadQuery-specific helpers.

### Documentation

- Describe build123d as the sole runtime included by the default installation.
- Mark CadQuery as an optional legacy/compatibility runtime.
- Update README installation, runtime docs, generated skill content, CLI help,
  and troubleshooting guidance with `agentcad[cadquery]` instructions.
- State that the optional extra introduces an additional third-party dependency
  and licensing surface.
- Add a customer-facing website page titled **"Incorporating AgentCAD into Your
  Project"**. Keep it concise and written for engineering/product teams rather
  than open-source licensing specialists. It should:
  - confirm that AgentCAD is Apache-2.0 and may be used in commercial projects;
  - distinguish using AgentCAD as an internal or hosted SaaS tool from shipping
    it in software delivered to customers;
  - explain that the default build123d installation excludes CadQuery, CasADi,
    COIN-OR, and METIS;
  - explain that `agentcad[cadquery]` is an optional compatibility profile with
    an additional dependency and licensing surface;
  - list the notices/attribution expected when AgentCAD is redistributed; and
  - include a plain disclaimer that the page is practical product guidance, not
    legal advice, and that adopters remain responsible for their own review.

## Acceptance criteria

1. In a fresh Python 3.10–3.12 environment, `pip install agentcad` does not
   install `cadquery` or `casadi`.
2. A dependency/SBOM check of that environment contains no CasADi-bundled
   COIN-OR or METIS binaries.
3. The complete build123d test suite passes with CadQuery absent.
4. STEP loading, render, inspect, measure, validate, diff, export, parts, daemon,
   and MCP smoke tests pass in the build123d-only environment.
5. Selecting CadQuery without the extra produces the documented structured
   installation error.
6. `pip install "agentcad[cadquery]"` restores the existing CadQuery runner and
   its compatibility tests pass unchanged or with documented intentional
   changes.
7. CLI help and generated documentation do not imply that CadQuery is included
   by default.
8. CI tests both dependency profiles independently; a full environment alone is
   insufficient because it can hide accidental CadQuery imports.
9. The "Incorporating AgentCAD into Your Project" website page is published,
   linked from installation/licensing documentation, and accurately describes
   both dependency profiles in customer-facing language.

## Compatibility and rollout

This changes fresh-install behavior for users who currently rely on CadQuery
without requesting an extra. Announce it in release notes and bump the minor
version. The migration command is:

```console
pip install "agentcad[cadquery]"
agentcad daemon restart
```

Upgrading an existing environment will not necessarily uninstall CadQuery or
CasADi, so release validation and license checks must use fresh environments.

Recommended delivery sequence:

1. Refactor shared STEP/render/daemon paths so build123d works with CadQuery
   physically absent.
2. Add missing-extra behavior and split CI profiles.
3. Change package metadata and documentation.
4. Publish the customer-facing "Incorporating AgentCAD into Your Project" page
   and link it from the README and website installation/licensing navigation.
5. Publish a pre-release and verify fresh installs on macOS, Linux, and Windows.
6. Release with an explicit compatibility note.

## Risks

- Hidden CadQuery imports may only appear in less common commands; separate
  no-CadQuery CI is the primary guardrail.
- Direct OCP STEP import may differ from CadQuery's handling of empty compounds,
  assemblies, colors, or malformed files. Existing failure-contract tests must
  be retained and expanded before switching loaders.
- Users may assume `agentcad` still includes CadQuery based on older docs or
  examples. The missing-extra error and release notes must be precise.
- The optional CadQuery graph remains subject to its own license review; this
  change isolates rather than resolves that graph.

## Decision requested

Approve `agentcad[cadquery]` as the compatibility boundary and make
build123d-only behavior the contract of the default PyPI installation.
