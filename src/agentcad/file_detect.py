"""File-type detection for graceful intake.

Sniffs extension + magic bytes before any kernel call. Never raises;
every input produces a structured classification dict.

Categories drive command-level behavior. Phase 1 of M60 edit journey.
"""
from pathlib import Path
from typing import Optional

# --- categories -------------------------------------------------------------

TIER0_BREP = "tier0_brep"               # STEP / BREP — full edit support
TIER1_MESH = "tier1_mesh"               # STL — inspect-only
TIER2_RECOGNIZED = "tier2_recognized"   # IGES / 3MF / OBJ / DXF / SVG — deferred
DISPLAY_FORMAT = "display_format"       # GLB / GLTF / PLY — export/display only
UNKNOWN_FORMAT = "unknown_format"       # extension not recognized
MALFORMED = "malformed"                 # extension says X, content says Y
EMPTY = "empty"                         # zero bytes
MISSING = "missing"                     # file does not exist
NOT_A_FILE = "not_a_file"               # directory, broken symlink, etc.


# --- format tables ----------------------------------------------------------

_EXT_TO_FORMAT = {
    ".step": "step", ".stp": "step",
    ".brep": "brep",
    ".stl": "stl",
    ".iges": "iges", ".igs": "iges",
    ".3mf": "3mf",
    ".obj": "obj",
    ".dxf": "dxf",
    ".svg": "svg",
    ".glb": "glb",
    ".gltf": "gltf",
    ".ply": "ply",
}

def is_recognized_cad_extension(path) -> bool:
    """True if the path has an extension agentcad recognizes as CAD-ish.
    Used by `agentcad run` to dispatch CAD files to import (Tier 0) or
    to surface graceful polite-no responses (Tier 1+) instead of letting
    the script runner try to parse it as Python."""
    p = Path(path) if not isinstance(path, Path) else path
    return p.suffix.lower() in _EXT_TO_FORMAT


_FORMAT_TO_CATEGORY = {
    "step": TIER0_BREP,
    "brep": TIER0_BREP,
    "stl": TIER1_MESH,
    "iges": TIER2_RECOGNIZED,
    "3mf": TIER2_RECOGNIZED,
    "obj": TIER2_RECOGNIZED,
    "dxf": TIER2_RECOGNIZED,
    "svg": TIER2_RECOGNIZED,
    "glb": DISPLAY_FORMAT,
    "gltf": DISPLAY_FORMAT,
    "ply": DISPLAY_FORMAT,
}

# Magic-byte format names that are *compatible* with a given extension.
# Used so we don't flag a real 3MF (ZIP container) as malformed.
_COMPATIBLE_MAGIC = {
    "3mf": {"zip"},
}


# --- public API -------------------------------------------------------------

def detect_file_type(path) -> dict:
    """Classify a file by extension + magic bytes. Never raises.

    Returns a dict with at minimum: ``category``, ``format``, ``extension``,
    ``size_bytes``. Additional keys (``expected_format``, ``reason``) appear
    on broken / malformed inputs.
    """
    p = Path(path) if not isinstance(path, Path) else path
    ext = p.suffix.lower()

    # Existence checks — must come first; broken symlinks raise on .stat().
    if not p.exists():
        if p.is_symlink():
            return {
                "category": NOT_A_FILE, "format": None, "extension": ext,
                "size_bytes": 0, "reason": "broken_symlink",
            }
        return {
            "category": MISSING, "format": None, "extension": ext,
            "size_bytes": 0,
        }
    if p.is_dir():
        return {
            "category": NOT_A_FILE, "format": None, "extension": "",
            "size_bytes": 0, "reason": "directory",
        }

    try:
        size = p.stat().st_size
    except OSError as exc:
        return {
            "category": NOT_A_FILE, "format": None, "extension": ext,
            "size_bytes": 0, "reason": str(exc),
        }

    if size == 0:
        return {
            "category": EMPTY, "format": _EXT_TO_FORMAT.get(ext),
            "extension": ext, "size_bytes": 0,
        }

    try:
        with p.open("rb") as f:
            head = f.read(512)
    except OSError as exc:
        return {
            "category": NOT_A_FILE, "format": None, "extension": ext,
            "size_bytes": size, "reason": str(exc),
        }

    detected_magic = _detect_by_magic(head)
    expected_format = _EXT_TO_FORMAT.get(ext)

    if expected_format is None:
        return {
            "category": UNKNOWN_FORMAT, "format": detected_magic,
            "extension": ext, "size_bytes": size,
        }

    if _is_malformed(expected_format, detected_magic):
        return {
            "category": MALFORMED, "format": detected_magic,
            "extension": ext, "size_bytes": size,
            "expected_format": expected_format,
        }

    return {
        "category": _FORMAT_TO_CATEGORY[expected_format],
        "format": expected_format,
        "extension": ext, "size_bytes": size,
    }


# --- internals --------------------------------------------------------------

def _detect_by_magic(head: bytes) -> Optional[str]:
    """Return a format name based on magic bytes, or None if not recognized."""
    if head.startswith(b"ISO-10303-21"):
        return "step"
    if head.startswith(b"DBRep_DrawableShape"):
        return "brep"
    if head.startswith(b"glTF"):
        return "glb"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(b"ply\n") or head.startswith(b"ply\r\n") or head.startswith(b"ply "):
        return "ply"

    try:
        text = head.decode("utf-8", errors="replace").lstrip().lower()
    except Exception:
        return None

    if text.startswith(("<!doctype html", "<html")):
        return "html"
    if text.startswith("<?xml") or text.startswith("<svg"):
        return "svg"
    if text.startswith("solid "):
        # ASCII STL. (Binary STL has no reliable magic; trust extension.)
        return "stl"
    return None


def _is_malformed(expected_format: str, detected_magic: Optional[str]) -> bool:
    """True if the magic bytes contradict what the extension claims."""
    if detected_magic is None:
        return False
    if detected_magic == expected_format:
        return False
    if detected_magic in _COMPATIBLE_MAGIC.get(expected_format, set()):
        return False
    return True
