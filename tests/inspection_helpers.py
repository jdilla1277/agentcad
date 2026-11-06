"""Request complete records in tests of contracts other than default delivery.

Default-budget tests invoke the real CLI/MCP without this compatibility option.
"""

from agentcad.inspection_delivery import OPTIONS
from pathlib import Path


def full_response_args(args):
    args = list(args)
    eligible = args and (args[0] == 'import' or args[0] == 'inspect' and '--report' in args)
    if eligible and '--full-response' not in args and not any(option in args for option in OPTIONS.values()):
        args.append('--full-response')
    return args


def snapshot_path(response):
    """Locate stored bytes for integrity assertions, even if inline path is omitted."""
    return Path(response['build_root']) / '.agentcad/inspection_delivery' / (response['response_snapshot']['id'] + '.json')
