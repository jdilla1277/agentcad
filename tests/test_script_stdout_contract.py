"""Script output never corrupts the JSON contract on stdout (#227).

CAD scripts print debug values, write to fd 1 directly, spawn child
processes, or use ``sys.stdout.buffer``. CliRunner only sees Python-level
``sys.stdout``, so these tests run real processes and parse the complete
stdout: the CLI for ``agentcad run``, and a real stdio session for the MCP
server, whose protocol stream must stay intact.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import agentcad
from agentcad.mcp.server import _invoke
from agentcad.native_io import capture_stdout

_SRC = str(Path(agentcad.__file__).resolve().parents[1])

_WRITES = {
    "print": 'print("print debug")\n',
    "raw_fd": 'import os\nos.write(1, b"raw debug\\n")\n',
    "child_process": (
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-c', 'print(\"child debug\")'], check=True)\n"
    ),
    "binary_buffer": 'import sys\nsys.stdout.buffer.write(b"buffer debug\\n")\n',
}
_EXPECTED = {
    "print": "print debug\n",
    "raw_fd": "raw debug\n",
    "child_process": "child debug\n",
    "binary_buffer": "buffer debug\n",
}


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [_SRC, env.get("PYTHONPATH")]))
    env["AGENTCAD_NO_LOG"] = "1"
    env.pop("AGENTCAD_DAEMON", None)
    return env


def _cli(*args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "agentcad", *args],
        capture_output=True, text=True, cwd=str(cwd), env=_env(), timeout=300,
    )


@pytest.fixture
def project(tmp_path):
    result = _cli("init", "--name", "p", cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    return tmp_path


@pytest.mark.parametrize("kind", sorted(_WRITES))
def test_cli_stdout_is_one_json_document(project, kind):
    (project / "s.py").write_text(f"{_WRITES[kind]}show_object(Box(1, 2, 3))\n")
    result = _cli("run", "s.py", "--dry-run", "--no-daemon", cwd=project)
    payload = json.loads(result.stdout)
    assert payload["status"] == "success", payload
    assert payload["script_output"] == _EXPECTED[kind]
    assert _EXPECTED[kind] not in result.stderr


def test_cli_failed_run_keeps_raw_output_before_the_error(project):
    (project / "s.py").write_text(
        "import os\n"
        "os.write(1, b'before crash\\n')\n"
        "if os.getpid() > 0:  # runtime condition, not statically dead code\n"
        "    raise ValueError('boom')\n"
        "show_object(Box(1, 2, 3))\n"
    )
    result = _cli("run", "s.py", "--label", "bad", "--no-daemon", cwd=project)
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["script_output"] == "before crash\n"


def test_capture_stdout_restores_fd_1(capfd):
    with capture_stdout() as captured:
        os.write(1, b"inside\n")
        print("printed")
        sys.stdout.buffer.write(b"binary\n")
    os.write(1, b"after\n")
    assert captured.text == "inside\nprinted\nbinary\n"
    assert capfd.readouterr().out == "after\n"


def test_mcp_invoke_parses_stdout_not_heartbeats(tmp_path):
    # run emits stderr heartbeats; the MCP result must come from stdout only.
    assert _cli("init", "--name", "p", cwd=tmp_path).returncode == 0
    (tmp_path / "s.py").write_text('print("mcp debug")\nshow_object(Box(1, 2, 3))\n')
    result = _invoke(["run", "s.py", "--dry-run", "--no-daemon"], cwd=str(tmp_path))
    assert result["command"] == "run"
    assert result["status"] == "success"
    assert result["script_output"] == "mcp debug\n"
    assert result["_exit_code"] == 0


def test_mcp_stdio_session_survives_script_output(project):
    """A real stdio MCP session: fd-level script writes must not reach the
    JSON-RPC stream, and the run result must carry them as script_output."""
    anyio = pytest.importorskip("anyio")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    (project / "s.py").write_text(
        "".join(_WRITES[kind] for kind in sorted(_WRITES))
        + "show_object(Box(1, 2, 3))\n"
    )
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "agentcad.mcp"], env=_env(),
    )

    async def call_run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("run", {
                    "script": "s.py", "cwd": str(project), "dry_run": True,
                    "preview": False, "diff": False, "view": False,
                })

    result = anyio.run(call_run)
    assert not result.isError
    payload = json.loads(result.content[0].text)
    assert payload["status"] == "success", payload
    for kind in sorted(_WRITES):
        assert _EXPECTED[kind] in payload["script_output"]


def test_isolate_protocol_stdout_moves_stray_fd_writes_to_stderr():
    """After isolation, raw fd-1 writes (OCCT, child processes) go to stderr
    while sys.stdout, which the stdio transport wraps, still reaches the
    original stdout."""
    snippet = (
        "import os, subprocess, sys\n"
        "from agentcad.mcp.server import isolate_protocol_stdout\n"
        "isolate_protocol_stdout()\n"
        "os.write(1, b'stray fd write\\n')\n"
        "subprocess.run([sys.executable, '-c', 'print(\"stray child\")'], check=True)\n"
        "sys.stdout.buffer.write(b'{\"jsonrpc\": \"2.0\"}\\n')\n"
        "sys.stdout.flush()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True, text=True, env=_env(), timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == '{"jsonrpc": "2.0"}\n'
    assert "stray fd write" in result.stderr
    assert "stray child" in result.stderr
