"""agentcad — CLI CAD tool for AI agents."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("agentcad")
except PackageNotFoundError:  # pragma: no cover - only hit in un-installed checkouts
    __version__ = "0.0.0+unknown"
