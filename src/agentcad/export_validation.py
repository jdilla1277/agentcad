"""Reporting helpers shared by run and standalone export."""


def compare_step_reports(before, after):
    """Compare layer outcomes/counts, not unstable face IDs or tessellation counts.

    A match is not a validity claim: two invalid reports may match, while STEP
    normalization can improve an invalid source. Only the reloaded artifact
    controls the existing run gate.

    A layer with status ``skipped`` on either side is left out of the
    comparison and listed in ``skipped_layers`` with the skipping side's
    message as ``skipped_reason``; ``is_valid`` is compared only when both
    reports reached a verdict, so a null ``before.is_valid`` caused by skipped
    layers is not a timeout. ``run`` skips the kernel check and the
    tessellation on large in-memory sources (see
    ``validation.round_trip_skip_layers``), so the reloaded STEP is the only
    side that always carries those layers.
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
    skipped = []
    skipped_reason = None
    unknown = False
    if a['solid_count'] is None or b['solid_count'] is None:
        unknown = True
    elif a['solid_count'] != b['solid_count']:
        differences.append({'field': 'solid_count', 'before': a['solid_count'], 'after': b['solid_count']})
    for name in sorted(a['layers'].keys() | b['layers'].keys()):
        left, right = a['layers'].get(name), b['layers'].get(name)
        if left == 'skipped' or right == 'skipped':
            skipped.append(name)
            if skipped_reason is None:
                side = before if left == 'skipped' else after
                skipped_reason = side.get('layers', {}).get(name, {}).get('message')
        elif left in (None, 'error', 'timeout') or right in (None, 'error', 'timeout'):
            unknown = True
        elif left != right:
            differences.append({'field': f'layers.{name}', 'before': left, 'after': right})
    if a['is_valid'] is None or b['is_valid'] is None:
        # A null verdict caused by an error or timeout is already unknown via
        # its layer; a null caused only by skipped layers is simply not
        # compared.
        if not skipped:
            unknown = True
    elif a['is_valid'] != b['is_valid']:
        differences.append({'field': 'is_valid', 'before': a['is_valid'], 'after': b['is_valid']})
    matches = False if differences else None if unknown else True
    if matches is True:
        message = 'STEP layer outcomes and solid count match the source.'
        if skipped:
            message = ('STEP layer outcomes and solid count match the source on the compared layers; '
                       + ', '.join(skipped) + ' not compared (not run on one side).')
    elif matches is False:
        message = ('STEP round-trip changed layer outcomes or solid count; review differences. '
                   'The reloaded STEP verdict still determines CAD validity.')
    else:
        message = 'STEP round-trip comparison is incomplete; no agreement claim is made.'
    return {'status': 'pass' if matches is True else 'fail' if matches is False else 'error',
            'matches': matches, 'before': a, 'after': b, 'differences': differences,
            'skipped_layers': skipped, 'skipped_reason': skipped_reason, 'message': message}


def mesh_warnings(reports):
    return [f'{fmt.upper()} was written but its mesh validation '
            f'{"failed" if report["is_valid"] is False else "is undetermined"}: '
            f'{report.get("message", report["status"])}'
            for fmt, report in reports.items() if report['is_valid'] is not True]
