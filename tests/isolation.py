"""Suite hermeticity: no test may read a `.env` from the checkout.

`config.credentials_file()` falls back to `./.env` for a standalone checkout — correct for
`make dev`, wrong for a test run: from a directory that happens to have a `.env`, `exchange`
finds credentials it was never given, `setup_state()` reports a configured install and exit
codes change. Every TestCase that touches configuration calls `apply(self)` so the file under
test is the one the test wrote.
"""
from __future__ import annotations

import os
import tempfile

_ENV_FILE = None


def path():
    """The credentials file for this test run (created empty on first use)."""
    global _ENV_FILE
    if _ENV_FILE is None:
        _ENV_FILE = os.path.join(tempfile.mkdtemp(prefix="linear-agent-tests-"), ".env")
    return _ENV_FILE


def apply(testcase):
    """Point `LINEAR_AGENT_ENV_FILE` at the isolated file for the duration of a TestCase."""
    saved = os.environ.get("LINEAR_AGENT_ENV_FILE")
    os.environ["LINEAR_AGENT_ENV_FILE"] = path()

    def restore():
        if saved is None:
            os.environ.pop("LINEAR_AGENT_ENV_FILE", None)
        else:
            os.environ["LINEAR_AGENT_ENV_FILE"] = saved

    testcase.addCleanup(restore)
    return path()
