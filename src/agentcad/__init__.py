"""agentcad — CLI CAD tool for AI agents."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

# Hardcoded fallback used when importlib metadata isn't available — typically a
# raw checkout where `pip install -e .` was never run, or a repository copy
# launched via `python -m agentcad` without the wheel registered. Keeping a
# real-looking identifier here means version-mismatch errors stay actionable
# instead of degrading to the opaque "0.0.0+unknown".
_FALLBACK_VERSION = "0.0.0+source"

try:
    __version__ = _pkg_version("agentcad")
except PackageNotFoundError:  # pragma: no cover - only hit in un-installed checkouts
    __version__ = _FALLBACK_VERSION
