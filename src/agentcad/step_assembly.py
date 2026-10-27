"""Read STEP assembly structure without flattening reusable components.

This is an intake primitive, not the versioned inspection manifest. Source
label IDs identify records within the exact input document only. Topology
indices address ``StepAssembly.shape`` through ``agentcad.topo_ids``; they
must not be carried across normalization or edits without verification.

OCCT's document reader supplies both the geometry and the source hierarchy
in one transfer. Definitions retain local geometry; occurrences retain their
path and composed assembly placement. No CadQuery dependency is required.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StepDefinition:
    source_id: str
    name: str | None
    name_status: str
    kind: str
    shape: Any = field(repr=False, compare=False)


@dataclass(frozen=True)
class StepOccurrence:
    """One source path; membership status applies only to the source transfer."""

    source_id: str
    definition_id: str
    parent_id: str | None
    name: str | None
    name_status: str
    # Row-major 3x4 affine matrix, in OCCT's normalized length units (mm).
    placement: tuple[tuple[float, ...], ...]
    solid_ids: tuple[int, ...]
    face_ids: tuple[int, ...]
    edge_ids: tuple[int, ...]
    mapping_status: str


@dataclass(frozen=True)
class StepAssembly:
    shape: Any = field(repr=False, compare=False)
    definitions: tuple[StepDefinition, ...]
    occurrences: tuple[StepOccurrence, ...]
    source_length_units: tuple[str, ...]
    hierarchy_status: str
    _document: Any = field(repr=False, compare=False)
    length_unit: str = "mm"


def read_step_assembly(path: str | Path) -> StepAssembly:
    """Read names, references, nested placements, and geometry from STEP.

    Source IDs are label entries/paths, never advertised version-local IDs.
    Leaf topology membership uses the same indexed maps as inspection. If
    coincident instances share topology, flag their mapping as ambiguous;
    distinct paths alone cannot make a raw numeric selector instance-specific.

    Raises a contextual ValueError for missing/malformed/empty input or a
    hierarchy whose geometry cannot be mapped exactly. Native parser output
    is sent to stderr, consistent with ``load_cad_shape``.
    """
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TColStd import TColStd_SequenceOfAsciiString
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDF import TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    from agentcad.native_io import silence_native_stdout
    from agentcad.step_io import _is_empty_compound
    from agentcad.topo_ids import _indexed_map

    p = Path(path)
    if not p.is_file():
        raise ValueError(f"CAD file not found: {p}")

    try:
        with silence_native_stdout():
            document = TDocStd_Document(TCollection_ExtendedString("XCAF"))
            # CAF transfer uses the document's target unit in preference to
            # the reader setting. This API takes metres: 0.001 m is 1 mm.
            XCAFDoc_DocumentTool.SetLengthUnit_s(document, 0.001)
            reader = STEPCAFControl_Reader()
            reader.SetNameMode(True)
            reader.SetColorMode(False)
            reader.SetLayerMode(False)
            if reader.ReadFile(str(p)) != IFSelect_RetDone:
                raise ValueError("STEP parser did not complete")
            reader.Reader().SetSystemLengthUnit(1.0)
            source_units = TColStd_SequenceOfAsciiString()
            reader.Reader().FileUnits(
                source_units,
                TColStd_SequenceOfAsciiString(),
                TColStd_SequenceOfAsciiString(),
            )
            if not reader.Transfer(document):
                raise ValueError("STEP document transfer did not complete")

        shape = reader.Reader().OneShape()
        if shape.IsNull() or _is_empty_compound(shape):
            raise ValueError("file contains no solids, shells, or faces")

        tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
        maps = tuple(
            _indexed_map(shape, kind)
            for kind in (TopAbs_SOLID, TopAbs_FACE, TopAbs_EDGE)
        )
        definitions: dict[str, StepDefinition] = {}
        occurrences: list[StepOccurrence] = []

        def visit(label, parent_id, parent_location, ancestors):
            from OCP.TDF import TDF_Label

            target = TDF_Label()
            if not tool.GetReferredShape_s(label, target):
                target = label
            definition_id = _label_id(target)
            if definition_id in ancestors:
                raise ValueError("cyclic STEP assembly reference")
            is_assembly = tool.IsAssembly_s(target)
            if definition_id not in definitions:
                definition_name = _label_name(target)
                definitions[definition_id] = StepDefinition(
                    source_id=definition_id,
                    name=definition_name,
                    name_status=_name_status(definition_name),
                    kind="assembly" if is_assembly else "part",
                    shape=tool.GetShape_s(target).Located(TopLoc_Location()),
                )
            source_id = "/".join(filter(None, (parent_id, _label_id(label))))
            location = parent_location.Multiplied(tool.GetLocation_s(label))
            intrinsic = definitions[definition_id].shape
            if intrinsic.IsNull():
                raise ValueError(f"component {source_id} has no geometry")

            memberships = ((), (), ())
            if not is_assembly:
                placed = intrinsic.Located(location)
                memberships = tuple(
                    _member_ids(placed, kind, index)
                    for kind, index in zip(
                        (TopAbs_SOLID, TopAbs_FACE, TopAbs_EDGE), maps
                    )
                )
            occurrence_name = _label_name(label)
            occurrences.append(StepOccurrence(
                source_id=source_id,
                definition_id=definition_id,
                parent_id=parent_id,
                name=occurrence_name,
                name_status=_name_status(occurrence_name),
                placement=_placement(location),
                solid_ids=memberships[0],
                face_ids=memberships[1],
                edge_ids=memberships[2],
                mapping_status="assembly" if is_assembly else "source_exact",
            ))
            if is_assembly:
                children = TDF_LabelSequence()
                tool.GetComponents_s(target, children)
                for i in range(1, children.Length() + 1):
                    visit(
                        children.Value(i), source_id, location,
                        ancestors | {definition_id},
                    )

        roots = TDF_LabelSequence()
        tool.GetFreeShapes(roots)
        for i in range(1, roots.Length() + 1):
            visit(roots.Value(i), None, TopLoc_Location(), frozenset())
        if not occurrences:
            raise ValueError("file has no addressable assembly roots")
        return StepAssembly(
            shape=shape,
            definitions=tuple(definitions.values()),
            occurrences=_mark_shared_memberships(occurrences),
            source_length_units=tuple(
                source_units.Value(i).ToCString()
                for i in range(1, source_units.Length() + 1)
            ),
            hierarchy_status=(
                "assembly_structure"
                if any(d.kind == "assembly" for d in definitions.values())
                else "flat_roots"
            ),
            _document=document,
        )
    except Exception as exc:
        raise ValueError(f"Could not read STEP assembly {p.name}: {exc}") from exc


def write_step_assembly(assembly: StepAssembly, path: str | Path) -> None:
    """Export the retained document, preserving definitions and references.

    Unlike exporting OneShape(), this retains source component names and the
    assembly tree. Re-read the result before assigning normalized entity IDs.
    """
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from agentcad.native_io import silence_native_stdout

    with silence_native_stdout():
        writer = STEPCAFControl_Writer()
        writer.SetNameMode(True)
        if not writer.Transfer(assembly._document, STEPControl_AsIs):
            raise RuntimeError("STEP assembly transfer did not complete")
        if writer.Write(str(path)) != IFSelect_RetDone:
            raise RuntimeError(f"STEP assembly write did not complete: {path}")


def _label_id(label) -> str:
    from OCP.TCollection import TCollection_AsciiString
    from OCP.TDF import TDF_Tool

    entry = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, entry)
    return entry.ToCString()


def _label_name(label) -> str | None:
    from OCP.TDataStd import TDataStd_Name

    name = TDataStd_Name()
    if not label.FindAttribute(TDataStd_Name.GetID_s(), name):
        return None
    # Preserve Unicode, punctuation, and spaces; source names are not IDs.
    value = name.Get().ToExtString()
    return value or None


def _name_status(name: str | None) -> str:
    """Keep raw labels, marking known generated patterns conservatively.

    ``document_label`` reports the presence of text, not proof that a human
    supplier authored it. Producers may generate names we do not recognize.
    """
    import re

    if name is None:
        return "missing"
    if re.fullmatch(r"=>\[[^\]]+\]|Open CASCADE STEP translator .*", name):
        return "generated_pattern"
    return "document_label"


def _placement(location) -> tuple[tuple[float, ...], ...]:
    transform = location.Transformation()
    return tuple(
        tuple(transform.Value(row, column) for column in range(1, 5))
        for row in range(1, 4)
    )


def _member_ids(shape, kind, index) -> tuple[int, ...]:
    from agentcad.topo_ids import _iter_indexed_map

    ids = tuple(index.FindIndex(member) for member in _iter_indexed_map(shape, kind))
    if 0 in ids:
        raise ValueError("source occurrence does not map to the loaded topology")
    return ids


def _mark_shared_memberships(occurrences) -> tuple[StepOccurrence, ...]:
    from dataclasses import replace

    owners: dict[int, list[int]] = {}
    for i, occurrence in enumerate(occurrences):
        for face_id in occurrence.face_ids:
            owners.setdefault(face_id, []).append(i)
    shared = {i for indices in owners.values() if len(indices) > 1 for i in indices}
    return tuple(
        replace(occurrence, mapping_status="shared_topology")
        if i in shared else occurrence
        for i, occurrence in enumerate(occurrences)
    )
