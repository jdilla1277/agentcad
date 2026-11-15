"""How sketch regions are named on maps and picked again in scripts.

A build script picks regions of a sketch, then the sketch gets edited and
the script reruns. The naming strategy decides what the map's ``selector``
says and how the script finds the same region after the edit.

``points``
    ``regions.at((x, y))``: the region containing a saved interior point.
    Needs nothing from the script. Survives small edits while the point
    stays inside its region; when a region moves out from under its point
    the neighbour is picked without an error.

``DEFAULT_NAMING`` picks the strategy; ``sketch_regions(naming=...)``
overrides it per call. Add a strategy by subclassing ``RegionNaming`` and
registering it in ``STRATEGIES``.

build123d runtime only; everything CAD-related is imported lazily.
"""

from __future__ import annotations

DEFAULT_NAMING = "points"


class RegionNaming:
    """A naming strategy: binds to one sketch_regions() result."""

    key = ""
    #: True when bind() needs to know which named curve each edge came from.
    needs_provenance = False

    def bind(self, *, faces, plane, curves, provenance):
        """A NamingContext for ``faces`` (the whole ordered region list)."""
        raise NotImplementedError


class NamingContext:
    """Naming facts for one sketch_regions() result."""

    def __init__(self, strategy, faces, plane):
        self.strategy = strategy
        self.faces = list(faces)
        self.plane = plane
        self._interiors = {}

    # --------------------------------------------------- shared helpers
    def interior(self, face):
        from agentcad.sketch_regions import interior_point

        key = _face_key(face)
        if key not in self._interiors:
            self._interiors[key] = interior_point(face, self.plane)
        return self._interiors[key]

    def map_info(self):
        """Map-level ``naming`` block for the region map JSON."""
        return {"strategy": self.strategy}

    def fields(self, face, interior):
        """Per-region naming fields for the map JSON (selector first)."""
        from agentcad.sketch_regions import region_selector

        return {"selector": region_selector(interior)}


class PointNaming(RegionNaming):
    """Selectors are ``regions.at((x, y))`` at each region's interior point."""

    key = "points"

    def bind(self, *, faces, plane, curves, provenance):
        return NamingContext(self.key, faces, plane)


STRATEGIES = {"points": PointNaming}


def resolve_naming(naming=None):
    """The RegionNaming for ``naming`` (None -> DEFAULT_NAMING)."""
    from agentcad.sketch_regions import SketchRegionError

    if naming is None:
        naming = DEFAULT_NAMING
    if isinstance(naming, RegionNaming):
        return naming
    if isinstance(naming, str) and naming in STRATEGIES:
        return STRATEGIES[naming]()
    raise SketchRegionError(
        f"sketch_regions() naming must be one of {', '.join(repr(k) for k in STRATEGIES)}, "
        f"got {naming!r}."
    )


def _face_key(face):
    return hash(face)


__all__ = [
    "DEFAULT_NAMING",
    "STRATEGIES",
    "RegionNaming",
    "PointNaming",
    "NamingContext",
    "resolve_naming",
]
