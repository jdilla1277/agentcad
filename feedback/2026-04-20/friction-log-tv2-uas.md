# Friction Log: Fixed-wing UAS (TV2) from SysMLv2 requirements

Source: remote feedback session submitted via `agentcad feedback`, delivered over Discord webhook on 2026-04-20.

Session stats: **Errors 0 | Successes 10 | Retries 2**

## What worked well (keep)
- Inline `agentcad docs` — reachable without leaving the shell
- JSON I/O on every command — scriptable end-to-end
- Daemon — warm imports made iteration fast
- Per-part colors via named `show_object` — made multi-part assemblies legible
- Helper library: `naca_wire` + `mirror_fuse` + `loft_sections` turned ~200 lines of raw OCP into ~20

## Friction points

### 1. Viewer HTML is a dotfile
`.agentcad_viewer_output.html` is awkward to share/upload — had to `cp` it to a clean name to push to the tracking system.
- **Suggestion:** write `preview.html` or `viewer.html` next to the STEP instead of a hidden file.

### 2. `--export` flag inconsistency between `run` and `export`
`agentcad run --export` help lists only `stl, glb`, but the `agentcad export` subcommand supports `obj` as well. The user only discovered OBJ was available by reading the subcommand docs.
- **Suggestion:** either add OBJ to `run --export`, or note the asymmetry in the `run --export` help text.

### 3. Top-view render looks asymmetric for a mirror-symmetric model
Model was mirror-symmetric about XZ (`is_valid: true`, bbox symmetric on Y), but the top view came out asymmetric. Front/iso looked correct.
- **Suggestion:** investigate camera/framing for the top view. Reporter offered a repro STEP.

### 4. `agentcad diff` is metrics-only
Metrics catch gross changes but don't show what actually moved between versions.
- **Suggestion:** overlay mode in `agentcad view` — load two STEPs tinted red/green so visual iteration is possible.

### 5. `elliptical_sweep` docs don't mention a minimum radius
Reporter used 3.0mm defensively at spine ends; smaller might work, smaller might blow up, but the docs didn't say.
- **Suggestion:** document the minimum radius (or the failure mode) in the `elliptical_sweep` helper docs.
