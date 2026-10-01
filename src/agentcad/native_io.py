"""Helpers for safe interaction with C-extension I/O.

OCP wraps OCCT (Open CASCADE), whose ``Message_DefaultMessenger`` writes
diagnostics directly to file descriptor 1 — bypassing Python's
``sys.stdout`` buffer entirely. That breaks any command whose contract is
"stdout is parseable JSON" because the C-level writes land *before* our
JSON, regardless of what we do at the Python level.

The fix is to redirect fd 1 → fd 2 around the OCCT call so the diagnostic
goes to stderr (where it belongs), leaving stdout clean for our JSON.
"""
import functools
import os
import sys
import tempfile
from contextlib import contextmanager


@functools.lru_cache(maxsize=1)
def _c_runtimes():
    """C runtimes whose stdio buffers native code may be writing through."""
    import ctypes

    names = ["ucrtbase", "msvcrt"] if os.name == "nt" else [None]
    runtimes = []
    for name in names:
        try:
            runtimes.append(ctypes.CDLL(name))
        except OSError:
            pass
    return tuple(runtimes)


def flush_native_stdio():
    """Flush C stdio buffers (printf, OCCT's std::cout) to the current fd 1.

    Native code writing through C stdio is buffered separately from Python.
    Flush while fd 1 still points where that output belongs; otherwise the
    bytes surface later — at exit, after the command's JSON on stdout.
    """
    for runtime in _c_runtimes():
        try:
            runtime.fflush(None)
        except (AttributeError, OSError):
            pass


@contextmanager
def silence_native_stdout():
    """Redirect fd 1 to fd 2 for the duration of the with block.

    Use around any OCCT / C-extension call that may write to native stdout.
    The CliRunner test harness can't catch these writes (it captures
    ``sys.stdout`` at the Python level); only a real subprocess sees them.
    See ``TestSubprocessContract`` in ``test_inspect_graceful.py``.
    """
    sys.stdout.flush()
    saved_fd = os.dup(1)
    try:
        os.dup2(2, 1)
        yield
    finally:
        flush_native_stdio()
        os.dup2(saved_fd, 1)
        os.close(saved_fd)


@contextmanager
def suppress_native_output():
    """Discard fd-level stdout/stderr around a fully handled native call.

    Use when the caller converts native failure into its own structured error
    and raw OCCT diagnostics would only duplicate that response or leak ANSI
    control sequences. Keep the scope tight so useful Python diagnostics are
    never hidden.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    saved_stdout = os.dup(1)
    saved_stderr = os.dup(2)
    null_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null_fd, 1)
        os.dup2(null_fd, 2)
        yield
    finally:
        flush_native_stdio()
        os.dup2(saved_stdout, 1)
        os.dup2(saved_stderr, 2)
        os.close(saved_stdout)
        os.close(saved_stderr)
        os.close(null_fd)


class CapturedOutput:
    """Text collected by :func:`capture_stdout`; filled in when the block exits."""

    text = ""


@contextmanager
def capture_stdout():
    """Collect everything written to stdout inside the block, at the fd level.

    Covers ``print()``, ``sys.stdout.buffer`` writes, raw ``os.write(1, ...)``,
    child processes, and C extensions (C stdio buffers are flushed into the
    capture before fd 1 is restored), so none of it can precede or follow a
    command's JSON on stdout. ``sys.stdout`` is a real line-buffered text file sharing
    the capture, so the normal text and ``.buffer`` interfaces keep working.
    If fd 1 is unusable, Python-level writes are still captured.
    """
    captured = CapturedOutput()
    with tempfile.TemporaryFile() as sink:
        stream = open(
            os.dup(sink.fileno()), "w", encoding="utf-8", errors="replace",
            buffering=1,
        )
        for existing in (sys.stdout, sys.__stdout__):
            if existing is not None:
                existing.flush()
        try:
            saved_fd = os.dup(1)
            os.dup2(sink.fileno(), 1)
        except OSError:
            saved_fd = None
        saved_stdout = sys.stdout
        sys.stdout = stream
        try:
            yield captured
        finally:
            sys.stdout = saved_stdout
            stream.close()
            if sys.__stdout__ is not None:
                try:
                    sys.__stdout__.flush()
                except (OSError, ValueError):
                    pass
            flush_native_stdio()
            if saved_fd is not None:
                os.dup2(saved_fd, 1)
                os.close(saved_fd)
            sink.seek(0)
            captured.text = sink.read().decode("utf-8", errors="replace")
