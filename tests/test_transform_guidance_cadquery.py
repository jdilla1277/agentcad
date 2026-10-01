"""Issue #203 on the CadQuery runtime: foreign translate forms get a repair.

Runs real scripts through the CadQuery runner so a change in runtime error
text or guidance ordering cannot silently drop the correction. Skipped
automatically when the optional ``cadquery`` extra is not installed (see the
root conftest).
"""

import cadquery  # noqa: F401  (marks this module as CadQuery-only)
import pytest

from agentcad.commands.run import _execution_error_guidance
from agentcad.runners import cadquery as cq_runner

_PREFIX = "part = cq.Workplane('XY').box(10, 20, 30)\n"


@pytest.mark.parametrize("expression,correction,more_at", [
    ("Translate((1, 2, 3))",
     "moved = translate(shape, (x, y, z))", "agentcad docs helpers"),
    ("part.val().translated((1, 2, 3))",
     "moved = translate(shape, (x, y, z))", "agentcad docs helpers"),
    ("part.val().translate(1, 2, 3)",
     "moved = shape.translate((x, y, z))", "agentcad docs helpers"),
    ("part.translate(1, 2, 3)",
     "moved = shape.translate((x, y, z))", "agentcad docs helpers"),
    ("translate(1, 2, 3)(part)",
     "Use translate(shape, (x, y, z))", "agentcad docs helpers"),
])
def test_cadquery_foreign_translate_forms_get_a_repair(expression, correction, more_at):
    source = f"{_PREFIX}result = {expression}\nshow_object(part)\n"
    result = cq_runner.execute(source)
    assert not result.success
    guidance = _execution_error_guidance(result.exception, "cadquery", source)
    assert correction in guidance["suggestion"], result.exception
    assert guidance["more_at"] == more_at


def test_cadquery_user_helper_translate_gets_no_cad_repair():
    source = (
        "class Helper:\n"
        "    def translate(self, vector):\n"
        "        return vector\n"
        "Helper().translate(1, 2, 3)\n"
        f"{_PREFIX}show_object(part)\n"
    )
    result = cq_runner.execute(source)
    assert not result.success
    assert _execution_error_guidance(result.exception, "cadquery", source) == {}
