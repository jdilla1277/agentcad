"""Build the example PCB enclosure from ``board.json``.

Run from the repository root:

    agentcad run examples/pcb_enclosure/enclosure.py --label pcb_v1 --render iso

The board spec is the single source of truth: it drives the tray cutouts,
standoffs, lid window, and keepouts. ``board.json`` documents the dimensions
taken from the drawing; ``README.md`` lists the assumptions used for the
dimensions the drawing does not pin down.
"""

from pathlib import Path

from agentcad.api import BoardSpec, pcb_case, pcb_lid, show_object

# `agentcad run` executes the script with the caller's working directory, so
# the repository-relative path works for the documented invocation. Keep the
# sibling-file fallback for running the script directly from this directory.
SPEC_PATH = Path("examples") / "pcb_enclosure" / "board.json"
if not SPEC_PATH.exists():
    SPEC_PATH = Path(__file__).with_name("board.json")

spec = BoardSpec.from_json(SPEC_PATH)
case = pcb_case(spec)
lid = pcb_lid(spec)

show_object(case, name="case")
show_object(lid, name="lid")
