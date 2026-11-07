"""Persistent plugin settings, so switching the agent needs no config editing.

`hermes linear-agent agent set cody` writes here. Precedence is:

    LINEAR_AGENT_PROFILE (env)  >  settings file  >  gateway default

Env wins because a container/CI deployment must be able to pin the value, and
`agent set` prints a warning when an env var is shadowing what it just wrote.

Location: $LINEAR_AGENT_SETTINGS_DIR, else Hermes' public plugin data dir when
running inside Hermes, else $HERMES_HOME/plugin-data/linear-agent, else
~/.hermes/plugin-data/linear-agent.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile

PLUGIN_ID = "linear-agent"
FILENAME = "settings.json"


def settings_dir():
    explicit = os.environ.get("LINEAR_AGENT_SETTINGS_DIR")
    if explicit:
        return explicit
    try:
        from plugins.plugin_storage import plugin_data_dir  # public plugin API

        return str(plugin_data_dir(PLUGIN_ID))
    except Exception:  # noqa: BLE001 - standalone / not inside Hermes
        pass
    home = os.environ.get("HERMES_HOME") or os.path.join(os.path.expanduser("~"), ".hermes")
    return os.path.join(home, "plugin-data", PLUGIN_ID)


def settings_path():
    return os.path.join(settings_dir(), FILENAME)


def read_settings(path=None):
    try:
        with open(path or settings_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_settings(values, path=None):
    """Atomic write, mode 0600 (it can carry deployment hints)."""
    target = path or settings_path()
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(values, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return target


def stored_profile():
    return str(read_settings().get("agent_profile") or "").strip().lower()


def set_stored_profile(profile):
    values = read_settings()
    values["agent_profile"] = (profile or "").strip().lower()
    return write_settings(values)


def env_shadows_profile():
    """True when an env var would override what `agent set` writes."""
    return bool((os.environ.get("LINEAR_AGENT_PROFILE") or "").strip())
