"""Suite hermeticity: no test may read a host credential store.

`read_setting` resolves through Hermes' `agent.secret_scope` inside Hermes, then the process
environment, then only the file named by `LINEAR_AGENT_ENV_FILE` when standalone. A test run
must not inherit any of those: a `.env` in the checkout, a real Hermes profile, or a stale
variable would turn a test's configuration into the machine's. Every TestCase that touches
configuration calls `apply(self)`, which names an empty credentials file for the standalone
path and hides any real `agent.secret_scope` for the duration of the test.
"""
from __future__ import annotations

import os
import sys
import tempfile
from unittest import mock

_ENV_FILE = None


def path():
    """The credentials file for this test run (created empty on first use)."""
    global _ENV_FILE
    if _ENV_FILE is None:
        _ENV_FILE = os.path.join(tempfile.mkdtemp(prefix="linear-agent-tests-"), ".env")
    return _ENV_FILE


def apply(testcase):
    """Isolate one TestCase from the host's Hermes scope and checkout `.env`."""
    saved = os.environ.get("LINEAR_AGENT_ENV_FILE")
    os.environ["LINEAR_AGENT_ENV_FILE"] = path()

    def restore():
        if saved is None:
            os.environ.pop("LINEAR_AGENT_ENV_FILE", None)
        else:
            os.environ["LINEAR_AGENT_ENV_FILE"] = saved

    testcase.addCleanup(restore)
    # `sys.modules["agent.secret_scope"] = None` makes `import agent.secret_scope` raise
    # ImportError, which is exactly the standalone path. A test that wants a fake scope
    # installs its own module later; patch.dict restores this sentinel afterwards.
    hidden = mock.patch.dict(sys.modules, {"agent": None, "agent.secret_scope": None})
    hidden.start()
    testcase.addCleanup(hidden.stop)
    return path()
