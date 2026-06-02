"""Helpers for safe interaction with C-extension I/O.

OCP wraps OCCT (Open CASCADE), whose ``Message_DefaultMessenger`` writes
diagnostics directly to file descriptor 1 — bypassing Python's
``sys.stdout`` buffer entirely. That breaks any command whose contract is
"stdout is parseable JSON" because the C-level writes land *before* our
JSON, regardless of what we do at the Python level.

The fix is to redirect fd 1 → fd 2 around the OCCT call so the diagnostic
goes to stderr (where it belongs), leaving stdout clean for our JSON.
"""
import os
import sys
from contextlib import contextmanager


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
        os.dup2(saved_fd, 1)
        os.close(saved_fd)
