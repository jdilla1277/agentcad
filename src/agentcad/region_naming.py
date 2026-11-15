"""How sketch regions are named on maps and picked again in scripts.

A build script picks regions of a sketch, then the sketch gets edited and
the script reruns. The naming strategy decides what the map's ``selector``
says and how the script finds the same region after the edit.

Two strategies, behind one interface:

``points``
    ``regions.at((x, y))``: the region containing a saved interior point.
    Needs nothing from the script. Survives small edits while the point
    stays inside its region; when a region moves out from under its point
    the neighbour is picked without an error.

``curves``
    ``regions.where(inside=["c1"], above="split")``: the region described by
    which side of each *named* input curve it lies on. Curves are named with
    keyword arguments: ``sketch_regions(rect=..., c1=..., split=...)``.
    Survives any edit that keeps the same curves crossing; when the
    description matches no region or several, the lookup raises and lists
    the candidates. Sketches without named curves fall back to points.

``DEFAULT_NAMING`` picks the strategy; ``sketch_regions(naming=...)``
overrides it per call. Add a strategy by subclassing ``RegionNaming`` and
registering it in ``STRATEGIES``.

build123d runtime only; everything CAD-related is imported lazily.
"""

from __future__ import annotations

import re

DEFAULT_NAMING = "curves"

_SIDES = ("inside", "outside", "above", "below", "left", "right")
_QUERY_KEYS = _SIDES + ("touching",)
_OPPOSITE = {
    "inside": "outside", "outside": "inside", "above": "below",
    "below": "above", "left": "right", "right": "left",
}


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

    def where(self, regions, query, *, every=False):
        from agentcad.sketch_regions import SketchRegionError

        raise SketchRegionError(
            "regions.where() needs named curves, and this sketch has none"
            + (" (region naming is set to 'points')" if self.strategy == "points" else "")
            + ". Name the curves with keywords, e.g. sketch_regions(rect=Rectangle(60, 30), "
            "hole=Circle(5)), or pick by point with regions.at((x, y))."
        )


class PointNaming(RegionNaming):
    """Selectors are ``regions.at((x, y))`` at each region's interior point."""

    key = "points"

    def bind(self, *, faces, plane, curves, provenance):
        return NamingContext(self.key, faces, plane)


class CurveNaming(RegionNaming):
    """Selectors are ``regions.where(...)`` over named input curves."""

    key = "curves"
    needs_provenance = True

    def bind(self, *, faces, plane, curves, provenance):
        if not curves:
            return NamingContext(self.key, faces, plane)
        return CurveContext(self.key, faces, plane, curves, provenance)


STRATEGIES = {"points": PointNaming, "curves": CurveNaming}


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


# ------------------------------------------------------------ curves
class NamedCurve:
    """One named input: its edges plus how a region can lie relative to it."""

    def __init__(self, name, faces, edges, plane):
        self.name = name
        self.edges = edges
        self.group = re.split(r"[\[.]", name, maxsplit=1)[0]
        self.areas = list(faces) or _closed_wire_faces(edges)
        self.line = None
        if not self.areas and len(edges) == 1 and edges[0].geom_type.name == "LINE":
            a = plane.to_local_coords(edges[0] @ 0)
            b = plane.to_local_coords(edges[0] @ 1)
            self.line = ((a.X, a.Y), (b.X, b.Y))

    def side(self, local, world, tolerance):
        """inside/outside, above/below or left/right of this curve; None if
        the curve has no sides (an open curve that is not a straight line)."""
        if self.areas:
            from agentcad.sketch_regions import _classify

            inside = any(_classify(area, world, tolerance) == "in" for area in self.areas)
            return "inside" if inside else "outside"
        if self.line is not None:
            (ax, ay), (bx, by) = self.line
            dx, dy = bx - ax, by - ay
            if abs(dx) >= abs(dy):
                return "above" if local[1] > ay + (local[0] - ax) * dy / dx else "below"
            return "right" if local[0] > ax + (local[1] - ay) * dx / dy else "left"
        return None


def _closed_wire_faces(edges):
    """Faces filling closed wires (e.g. Polyline(..., close=True)); [] if open."""
    from build123d import Face, Wire

    try:
        wires = Wire.combine(edges)
    except Exception:  # noqa: BLE001 — not combinable: treat as open
        return []
    closed = [wire for wire in wires if wire.is_closed]
    if len(closed) != len(wires):
        return []
    try:
        return [Face(wire) for wire in closed]
    except Exception:  # noqa: BLE001 — non-planar or degenerate wire
        return []


def _face_key(face):
    return hash(face)


class CurveContext(NamingContext):
    """Signatures, edge lists and shortest unique selectors for named curves."""

    def __init__(self, strategy, faces, plane, curves, provenance):
        super().__init__(strategy, faces, plane)
        self.curves = curves
        self.provenance = provenance
        self._records = None
        self._by_key = {}
        self._rank = {}
        for position, curve in enumerate(curves):
            self._rank.setdefault(curve.name, position)

    def map_info(self):
        info = {"strategy": self.strategy, "curves": [curve.name for curve in self.curves]}
        groups = sorted({c.group for c in self.curves if c.group != c.name})
        if groups:
            info["groups"] = groups
        return info

    # ---------------------------------------------------- per region
    def records(self):
        if self._records is None:
            from agentcad.sketch_regions import _faces_extent

            tolerance = max(1e-7, 1e-6 * _faces_extent(self.faces))
            self._records = [
                self._record(index, face, tolerance) for index, face in enumerate(self.faces)
            ]
            self._by_key = {}
            for record in self._records:
                self._by_key.setdefault(record["key"], record)
            # term -> positions of the regions it holds for (members only;
            # group names are a lookup convenience, not part of selectors).
            # Each set is a bitmask: bit i is set when the term holds for
            # regions[i], so "which regions match all these terms" is an AND.
            self._term_sets = {}
            for position, record in enumerate(self._records):
                for term in _own_terms(record):
                    self._term_sets[term] = self._term_sets.get(term, 0) | (1 << position)
            for position, record in enumerate(self._records):
                record["selector"] = self._shortest(position, record)
        return self._records

    def record_for(self, face):
        self.records()
        return self._by_key.get(_face_key(face))

    def _record(self, index, face, tolerance):
        local = self.interior(face)
        world = self.plane.from_local_coords(local)
        sides = {}
        for curve in self.curves:
            side = curve.side(local, world, tolerance)
            if side is not None:
                sides[curve.name] = side
        cycle = self._edge_cycle(face, sides)
        touching = sorted({
            self.provenance[edge] for edge in face.edges() if edge in self.provenance
        }, key=self._order)
        return {
            "key": _face_key(face), "index": index, "face": face, "interior": local,
            "sides": sides, "edges": cycle, "touching": touching,
        }

    def _order(self, name):
        return self._rank.get(name, len(self.curves))

    def _edge_cycle(self, face, sides):
        from build123d import Edge
        from OCP.BRepTools import BRepTools_WireExplorer

        cycle = []
        explorer = BRepTools_WireExplorer(face.outer_wire().wrapped)
        while explorer.More():
            name = self.provenance.get(Edge(explorer.Current()))
            item = [name, sides.get(name) if name else None]
            if not cycle or cycle[-1] != item:
                cycle.append(item)
            explorer.Next()
        if len(cycle) > 1 and cycle[0] == cycle[-1]:
            cycle.pop()
        if not cycle:
            return cycle
        rotations = [cycle[i:] + cycle[:i] for i in range(len(cycle))]
        return min(rotations, key=lambda items: [(self._order(n), s or "") for n, s in items])

    def signature(self, record):
        out = {}
        for name, side in record["sides"].items():
            out.setdefault(side, []).append(name)
        return {key: out[key] for key in _SIDES if key in out}

    # ------------------------------------------------------ matching
    def _matches(self, record, terms):
        for key, name in terms:
            members = [c.name for c in self.curves if c.name == name or c.group == name]
            if key == "touching":
                if not any(member in record["touching"] for member in members):
                    return False
                continue
            states = [record["sides"].get(member) for member in members]
            if key == "outside" or key in ("above", "below", "left", "right"):
                if not states or not all(state == key for state in states):
                    return False
            elif not any(state == key for state in states):
                return False
        return True

    def _shortest(self, position, record):
        """Irreducible set of terms that matches this region and no other.

        Every ``inside`` term stays: it says which shapes contain the region,
        and without it an edit that moves a shape off the outline could leave
        a shorter description matching some other region without an error
        (``inside=["hole"]`` would pick the disk once the hole leaves the
        plate; ``inside=["rect", "hole"]`` refuses). Other terms are dropped
        while the match stays unique: contacts first (they change when a
        curve starts or stops touching), then sides in curve order.
        """
        sides = [(side, name) for name, side in record["sides"].items()]
        touching = [("touching", name) for name in record["touching"]]
        terms = sides + touching
        target = 1 << position
        masks = self._term_sets
        everything = (1 << len(self._records)) - 1

        kept = everything  # AND of the terms that always stay
        for term in sides:
            if term[0] == "inside":
                kept &= masks[term]
        droppable = touching + [term for term in sides if term[0] != "inside"]
        # after[k]: AND of droppable[k:], the terms not yet tried.
        after = [everything] * (len(droppable) + 1)
        for k in range(len(droppable) - 1, -1, -1):
            after[k] = after[k + 1] & masks[droppable[k]]
        if kept & after[0] != target:
            return None
        remaining = len(terms)
        dropped = set()
        for k, term in enumerate(droppable):
            # Drop the term if the kept ones plus the untried ones still
            # pick this region alone (and at least one term is left).
            if remaining > 1 and kept & after[k + 1] == target:
                dropped.add(term)
                remaining -= 1
            else:
                kept &= masks[term]
        return self._collapse_groups([t for t in terms if t not in dropped])

    def _collapse_groups(self, terms):
        """outside=["holes[0]", "holes[1]", "holes[2]"] -> outside=["holes"]."""
        groups = {}
        for curve in self.curves:
            if curve.group != curve.name:
                groups.setdefault(curve.group, []).append(curve.name)
        for group, members in groups.items():
            for key in ("outside", "above", "below", "left", "right"):
                if all((key, member) in terms for member in members):
                    first = terms.index((key, members[0]))
                    terms = [t for t in terms if t not in {(key, m) for m in members}]
                    terms.insert(min(first, len(terms)), (key, group))
        return terms

    # ------------------------------------------------- map fields
    def fields(self, face, interior):
        from agentcad.sketch_regions import region_selector

        point = region_selector(interior)
        record = self.record_for(face)
        if record is None:
            return {"selector": point}
        out = {}
        terms = record["selector"]
        out["selector"] = _format_where(terms) if terms else point
        out["point_selector"] = point
        out["signature"] = self.signature(record)
        out["edges"] = record["edges"]
        return out

    # -------------------------------------------------------- lookup
    def where(self, regions, query, *, every=False):
        from agentcad.sketch_regions import SketchRegionError, make_region_list

        terms = _query_terms(query)
        known = {c.name for c in self.curves} | {c.group for c in self.curves}
        unknown = [name for _key, name in terms if name not in known]
        if unknown:
            raise SketchRegionError(
                f"regions.where(): no curve named {', '.join(repr(n) for n in unknown)}; "
                f"this sketch names {', '.join(c.name for c in self.curves)}."
            )
        allowed = {_face_key(face) for face in regions}
        records = [r for r in self.records() if r["key"] in allowed]
        hits = [r for r in records if self._matches(r, terms)]
        call = _format_where(terms)
        if not hits:
            raise SketchRegionError(
                f"{call} matches no region. The curves no longer cross the way "
                f"this description assumes{self._nearest_hint(records, terms)}. "
                "Inspect the new map and pick again."
            )
        if every:
            return make_region_list([r["face"] for r in hits], self.plane, naming=self)
        if len(hits) > 1:
            listed = "; ".join(
                f"regions[{r['index']}] at {_fmt_point(r['interior'])}: "
                + (_format_where(r["selector"]) if r["selector"] else f"regions.at({_fmt_point(r['interior'])})")
                for r in hits[:6]
            )
            more = f" (+{len(hits) - 6} more)" if len(hits) > 6 else ""
            raise SketchRegionError(
                f"{call} matches {len(hits)} regions: {listed}{more}. Add a term "
                "that tells them apart, or use regions.where_all(...) to take all of them."
            )
        return hits[0]["face"]

    def _nearest_hint(self, records, terms):
        best = None
        for record in records:
            met = sum(self._matches(record, [term]) for term in terms)
            if best is None or met > best[0]:
                best = (met, record)
        if best is None or best[0] == 0:
            return ""
        missing = [t for t in terms if not self._matches(best[1], [t])]
        if not missing:
            return ""
        wrong = ", ".join(f"{key}={name!r}" for key, name in missing)
        return f" (closest: regions[{best[1]['index']}], which fails {wrong})"


def _own_terms(record):
    return [(side, name) for name, side in record["sides"].items()] + [
        ("touching", name) for name in record["touching"]
    ]


def _query_terms(query):
    from agentcad.sketch_regions import SketchRegionError

    terms = []
    for key, value in query.items():
        if key not in _QUERY_KEYS:
            raise SketchRegionError(
                f"regions.where() got {key}=; use {', '.join(k + '=' for k in _QUERY_KEYS)}."
            )
        if value is None:
            continue
        names = [value] if isinstance(value, str) else list(value)
        for name in names:
            if not isinstance(name, str):
                raise SketchRegionError(
                    f"regions.where() {key}= takes curve names (strings), got {name!r}."
                )
            terms.append((key, name))
    if not terms:
        raise SketchRegionError(
            "regions.where() needs at least one term, e.g. "
            "regions.where(inside=[\"c1\"], above=\"split\")."
        )
    for key, name in terms:
        if (_OPPOSITE.get(key), name) in terms:
            raise SketchRegionError(
                f"regions.where() asks for both {key}= and {_OPPOSITE[key]}= of {name!r}."
            )
    return terms


def _format_where(terms):
    groups = {}
    for key, name in terms:
        groups.setdefault(key, []).append(name)
    parts = []
    for key in _QUERY_KEYS:
        if key not in groups:
            continue
        names = groups[key]
        if key in ("above", "below", "left", "right") and len(names) == 1:
            parts.append(f'{key}="{names[0]}"')
        else:
            parts.append(f"{key}=[" + ", ".join(f'"{n}"' for n in names) + "]")
    return f"regions.where({', '.join(parts)})"


def _fmt_point(point):
    return "(" + ", ".join(f"{round(float(v), 3):g}" for v in point[:2]) + ")"


__all__ = [
    "DEFAULT_NAMING",
    "STRATEGIES",
    "RegionNaming",
    "PointNaming",
    "CurveNaming",
    "NamingContext",
    "resolve_naming",
]
