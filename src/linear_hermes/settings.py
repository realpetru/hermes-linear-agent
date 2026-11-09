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


def hermes_home():
    """The Hermes profile home: $HERMES_HOME, else ~/.hermes (the CLI's own default)."""
    return os.environ.get("HERMES_HOME") or os.path.join(os.path.expanduser("~"), ".hermes")


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


def stored_routing():
    """Label -> profile map from the settings file, normalised, junk dropped."""
    raw = read_settings().get("routing")
    if not isinstance(raw, dict):
        return {}
    clean = {}
    for label, profile in raw.items():
        if isinstance(label, str) and isinstance(profile, str):
            if label.strip() and profile.strip():
                clean[label.strip().lower()] = profile.strip().lower()
    return clean


def set_stored_routing(mapping):
    values = read_settings()
    clean = {}
    for label, profile in (mapping or {}).items():
        if isinstance(label, str) and isinstance(profile, str):
            if label.strip() and profile.strip():
                clean[label.strip().lower()] = profile.strip().lower()
    values["routing"] = clean
    return write_settings(values)


def stored_public_url():
    """The public base URL setup remembered, or ""."""
    return str(read_settings().get("public_url") or "").strip().rstrip("/")


def set_stored_public_url(url):
    values = read_settings()
    clean = (url or "").strip().rstrip("/")
    if clean:
        values["public_url"] = clean
    else:
        values.pop("public_url", None)
    return write_settings(values)


def stored_state_key():
    """The key that signs the OAuth install link, or "".

    It is deliberately not a credential store: it never leaves this 0600 settings file, and
    it only proves the callback came back to a link this plugin minted.
    """
    return str(read_settings().get("state_key") or "").strip()


def ensure_state_key():
    """Return the install-link key, generating and persisting one on first use."""
    current = stored_state_key()
    if current:
        return current
    value = os.urandom(32).hex()
    values = read_settings()
    values["state_key"] = value
    write_settings(values)
    return value


def env_shadows_profile():
    """True when an env value (profile scope or process env) would override `agent set`."""
    from .secrets import read_setting

    return bool((read_setting("LINEAR_AGENT_PROFILE", "") or "").strip())
