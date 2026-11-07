"""Read settings the way Hermes wants credentials read.

Catalog rule 11: a plugin's secrets come through Hermes, never a bare ``os.environ``
read, because that can pick up another profile's key under multiplexing. Hermes
exposes ``agent.secret_scope.get_secret`` (a public plugin API module), so we prefer
it and fall back to the environment only when Hermes is not importable (standalone
``make dev``, unit tests).
"""
from __future__ import annotations

import os


def read_setting(name, default=None, env=None):
    """Resolve NAME through Hermes' secret scope, then the given mapping, then os.environ.

    ``env`` exists so tests can inject a mapping without touching the process env.
    """
    if env is not None and name in env:
        return env[name]
    try:
        from agent.secret_scope import get_secret  # public plugin API

        value = get_secret(name, None)
        if value is not None:
            return value
    except Exception:  # noqa: BLE001 - not running inside Hermes
        pass
    if env is not None:
        return default
    return os.environ.get(name, default)
