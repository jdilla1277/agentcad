# CAD recipes

Reusable features and complete models for people and agents to copy, combine,
and adapt. Start with the closest recipe, copy its directory into your project,
and change the dimensions and geometry to suit your design.

| Recipe | What it builds | Runtime |
| --- | --- | --- |
| [PCB enclosure](pcb_enclosure/) | A tray and screw-on lid with board supports, connector openings, and windows | build123d |

Recipes are source examples in this repository. Install agentcad separately,
then copy or download the recipe you want. Each directory documents its own
run command and assumptions. Recipe-specific helpers and input formats live
beside the model and can evolve with it. They are not exported by `agentcad.api`.

## Recipe layout

Use one directory per feature or model:

```text
recipes/<name>/
  README.md       # Purpose, customization points, assumptions, and run commands
  preview.png     # A render of the example, with its arrangement explained
  example.py      # A complete script that runs with agentcad
  <feature>.py    # Local reusable geometry helpers, when useful
  checks.json     # Measurable expectations for the example
  tests/          # Focused checks for geometry and meaningful edge cases
```

Add a sample input file when the recipe needs one. Small features can keep
their dimensions in `example.py`; a separate JSON format is optional.

## Contributing a recipe

1. Name the feature or model and add it to the table above.
2. Include a working example, a preview, the required runtime, and a command
   someone can run after copying the directory. Keep helper imports and inputs
   local so the copy does not depend on this repository's directory layout.
3. Name the dimensions someone is likely to change. Explain the coordinate
   frame, units, supported variations, and dimensions that must change together.
4. Record assumptions and limitations, including hardware and manufacturing
   choices. Distinguish supplied measurements from example values.
5. Include checks for the exported example and focused tests that support the
   claimed behavior. Say what the checks establish and what still needs review.
   Run tests with `python -m pytest recipes/<name>` from the repository root.

The normal test suite includes `recipes/`. Generated STEP files, build
directories, and temporary renders stay out of Git; commit the named preview.

General execution, measurement, export, and validation capabilities belong in
agentcad itself. A recipe can use those capabilities while keeping its design
choices local. Shared helpers can be considered for the public API as repeated
use establishes a stable, broadly useful contract.
