"""Read settings the way Hermes wants credentials read.

Catalog rule 11: a plugin's secrets come through Hermes' public secret scope, never a bare
``os.environ`` read or a shared ``.env`` file. Hermes exposes
``agent.secret_scope.get_secret`` (a public plugin API module), so inside Hermes that is the
only source. Hermes loads the active profile's ``.env`` into that scope itself.

Standalone (the bridge outside Hermes) has no scope: the process environment, then the file
the user explicitly names with ``LINEAR_AGENT_ENV_FILE``. There is deliberately no implicit
``$HERMES_HOME/.env`` or ``./.env`` fallback — those files are Hermes' and a random checkout's,
not this plugin's.
"""
from __future__ import annotations

import os

# Names whose value is a credential. Inside Hermes a miss must fail closed: falling back to
# ``os.environ`` could read another profile's value while multiplexing.
SECRET_NAMES = frozenset({
    "LINEAR_CLIENT_SECRET",
    "LINEAR_WEBHOOK_SECRET",
    "LINEAR_STATE_KEY",
    "LINEAR_API_KEY",
    "HERMES_GATEWAY_TOKEN",
    "STATUS_TOKEN",
})


def _named_file(name, default):
    """The file the user named, if any. Never an implicit path."""
    from .config import credentials_file, load_dotenv

    path = credentials_file()
    if not path:
        return default
    return load_dotenv(path).get(name, default)


def read_setting(name, default=None, env=None):
    """Resolve NAME through Hermes' secret scope, or the standalone sources.

    ``env`` exists so tests and internal callers can inject a mapping. It is authoritative:
    a missing key means a missing value, never a reason to consult the host's environment.
    """
    if env is not None:
        return env[name] if name in env else default
    try:
        from agent.secret_scope import get_secret  # public plugin API
    except ImportError:
        value = os.environ.get(name)
        if value is not None:
            return value
        return _named_file(name, default)
    try:
        value = get_secret(name, None)
    except Exception:  # noqa: BLE001 - a scoped miss must never borrow the ambient env
        value = None
    if value is not None:
        return value
    if name in SECRET_NAMES:
        return default
    # Operational settings (PORT, BIND, LINEAR_AGENT_*) are not credentials; keep them
    # readable from the process environment so `serve --port` and systemd units still work.
    return os.environ.get(name, default)
