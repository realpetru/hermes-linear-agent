"""linear-agent — make Hermes a first-class Linear agent.

Two halves ship together:

* the **bridge** (``linear_hermes`` package): a small, dependency-free HTTP service
  that verifies Linear's ``Linear-Signature``, turns an AgentSessionEvent into a
  Hermes run, and streams ``thought`` / ``action`` / ``response`` activities back to
  the issue thread under the agent's own app identity;
* the **plugin surface** (this module): tools to inspect the bridge and generate the
  OAuth install URL, a bundled setup skill, and ``hermes linear-agent serve``.

Everything goes through the public plugin API (``ctx.register_tool``,
``ctx.register_skill``, ``ctx.register_cli_command``). Nothing patches Hermes core.

The bridge lives under ``src/`` (a normal src layout so it also installs with pip).
Hermes loads this directory as a package, so the plugin's own source tree is put on
``sys.path`` before importing it — nothing else is touched.
"""
from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

PLUGIN_VERSION = "1.3.4"

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def register(ctx):
    """Called once by Hermes at startup. Must never raise."""
    try:
        from linear_hermes.plugin_api import register_plugin_surface

        register_plugin_surface(ctx)
        logger.debug("linear-agent %s registered", PLUGIN_VERSION)
    except Exception:  # noqa: BLE001 - a broken plugin must not break Hermes
        logger.exception("linear-agent failed to register")
