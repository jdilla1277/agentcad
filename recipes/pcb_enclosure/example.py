"""Build the PCB enclosure. Run ``agentcad run example.py`` in this directory."""

from pathlib import Path
from runpy import run_path

from agentcad.api import show_object

# The CLI executes scripts in the caller's working directory. Load the local
# helper file directly so copied recipes also work in a warm agentcad daemon.
# run_path avoids relying on an installed package or a cached sibling import.
recipe_dir = Path(__file__).resolve().parent
helpers = run_path(str(recipe_dir / "enclosure.py"))
spec = helpers["BoardSpec"].from_json(recipe_dir / "board.json")

show_object(helpers["pcb_case"](spec), name="case")
show_object(helpers["pcb_lid"](spec), name="lid")
