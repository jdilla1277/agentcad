"""Reporting helpers shared by run and standalone export."""


def compare_step_reports(before, after):
    """Compare layer outcomes/counts, not unstable face IDs or tessellation counts.

    A match is not a validity claim: two invalid reports may match, while STEP
    normalization can improve an invalid source. Only the reloaded artifact
    controls the existing run gate.
    """
    def summary(report):
        return {
            'is_valid': report.get('is_valid'),
            'solid_count': report.get('layers', {}).get('structure', {}).get('solid_count'),
            'layers': {name: entry['status'] for name, entry in report.get('layers', {}).items()
                       if name not in ('file_parse', 'kernel_load')},
        }
    a, b = summary(before), summary(after)
    differences = []
    unknown = a['is_valid'] is None or b['is_valid'] is None
    for field in ('is_valid', 'solid_count'):
        if a[field] is None or b[field] is None:
            unknown = True
        elif a[field] != b[field]:
            differences.append({'field': field, 'before': a[field], 'after': b[field]})
    for name in sorted(a['layers'].keys() | b['layers'].keys()):
        left, right = a['layers'].get(name), b['layers'].get(name)
        if left in (None, 'error', 'timeout') or right in (None, 'error', 'timeout'):
            unknown = True
        elif left != right:
            differences.append({'field': f'layers.{name}', 'before': left, 'after': right})
    matches = False if differences else None if unknown else True
    return {'status': 'pass' if matches is True else 'fail' if matches is False else 'error',
            'matches': matches, 'before': a, 'after': b, 'differences': differences,
            'message': ('STEP layer outcomes and solid count match the source.' if matches is True else
                        'STEP round-trip changed layer outcomes or solid count; review differences. '
                        'The reloaded STEP verdict still determines CAD validity.' if matches is False else
                        'STEP round-trip comparison is incomplete; no agreement claim is made.')}


def mesh_warnings(reports):
    return [f'{fmt.upper()} was written but its mesh validation '
            f'{"failed" if report["is_valid"] is False else "is undetermined"}: '
            f'{report.get("message", report["status"])}'
            for fmt, report in reports.items() if report['is_valid'] is not True]
