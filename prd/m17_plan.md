# M17 — Friction Fixes

## Context

A Golden Gate Bridge friction log exposed five issues after an agent used cadtool for a real multi-part model. The critical issue — silent data loss when using multiple `show_object()` calls — cost 2 wasted iterations. The remaining issues are documentation gaps.

## Fixes

| # | Priority | Issue | Fix |
|---|----------|-------|-----|
| 1 | CRITICAL | Multiple `show_object()` silently drops all but the first | Auto-compound in run.py |
| 2 | MEDIUM | No example script in docs | Add "quickstart" section |
| 3 | MEDIUM | Units never mentioned | Add units note to "metrics" section |
| 4 | MEDIUM | `tapered_sweep` distortion at sharp corners undocumented | Add limitation note to "helpers" section |
| 5 | LOW | Helper output → `show_object` type conversion undocumented | Add conversion patterns to "helpers" section |

## Files Modified

| File | Changes |
|------|---------|
| `app/src/cadtool/commands/run.py` | Auto-compound logic + warning field |
| `app/src/cadtool/commands/docs.py` | New "quickstart" section, updated "metrics" and "helpers" sections |
| `app/tests/test_run.py` | 7 new multi-show_object tests |
| `app/tests/test_docs.py` | 5 new docs tests + updated sections list test |

## Test Count

190 → 202 (+12 tests)
