"""Bounded occupied-volume checks for candidate pairs of authored parts.

The batch worker retains completed pairs on timeout. Unfinished pairs never
become evidence of contact or absence of overlap.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

MAX_PAIRS = 128
TIMEOUT_S = 5.0


def bounded_overlaps(shapes, pairs, *, max_pairs=MAX_PAIRS, timeout_s=TIMEOUT_S):
    results = {pair: {'status': 'unavailable', 'reason': 'pair_limit'} for pair in pairs}
    selected = pairs[:max_pairs]
    if not selected:
        return results
    with tempfile.TemporaryDirectory(prefix='agentcad-part-overlap-') as directory:
        root = Path(directory)
        try:
            from OCP.BRepTools import BRepTools
            for i in sorted({i for pair in selected for i in pair}):
                if not BRepTools.Write_s(shapes[i], str(root / f'{i}.brep')):
                    raise ValueError('Could not serialize part')
            (root / 'pairs.json').write_text(json.dumps(selected))
        except Exception:
            results.update({pair: {'status': 'unavailable', 'reason': 'input_serialization_failed'} for pair in selected})
            return results
        reason = 'worker_failed'
        try:
            completed = subprocess.run([sys.executable, '-m', 'agentcad.part_overlap', str(root)],
                                       cwd=root,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                       env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1]) + os.pathsep + os.environ.get('PYTHONPATH', '')},
                                       timeout=timeout_s, check=False)
            reason = 'worker_failed' if completed.returncode else 'worker_result_missing'
        except subprocess.TimeoutExpired:
            reason = 'timeout'
        except OSError:
            pass
        results.update({pair: {'status': 'unavailable', 'reason': reason} for pair in selected})
        output = root / 'results.jsonl'
        if output.exists():
            for line in output.read_text().splitlines():
                try:
                    row = json.loads(line)
                    pair = tuple(row.pop('pair'))
                    if pair not in selected:
                        continue
                    if row.get('status') == 'complete':
                        if not all(math.isfinite(row[k]) and row[k] >= 0 for k in ('volume_mm3', 'volume_tolerance_mm3')):
                            continue
                    elif row.get('status') != 'unavailable':
                        continue
                    results[pair] = row
                except (ValueError, KeyError, TypeError):
                    continue
    return results


def main():
    from agentcad.solid_compare import compare_solid_volumes, _volume
    from agentcad.step_io import load_cad_shape
    root = Path(sys.argv[1])
    shapes = {}
    with (root / 'results.jsonl').open('w') as output:
        for pair in json.loads((root / 'pairs.json').read_text()):
            try:
                for i in pair:
                    if i not in shapes:
                        shapes[i] = load_cad_shape(root / f'{i}.brep')
                comparison = compare_solid_volumes(*(shapes[i] for i in pair))
                if not comparison.available:
                    row = {'status': 'unavailable', 'reason': comparison.data.get('reason', 'solid_comparison_unavailable')}
                else:
                    volume = _volume(comparison.shared_shape)
                    tolerance = max(1e-6, max(comparison.data['volumes']['reference'], comparison.data['volumes']['candidate']) * 1e-12)
                    if not math.isfinite(volume) or volume < 0:
                        raise ValueError('Invalid intersection volume')
                    row = {'status': 'complete', 'volume_mm3': volume, 'volume_tolerance_mm3': tolerance}
            except Exception:
                row = {'status': 'unavailable', 'reason': 'solid_comparison_failed'}
            output.write(json.dumps({'pair': pair, **row}) + '\n')
            output.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
