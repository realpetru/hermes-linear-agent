"""Discover and validate Hermes agent profiles.

A profile is a named Hermes agent with its own config, skills and memory. The bridge
can answer Linear as a specific one instead of whatever the gateway defaults to.
"""
from __future__ import annotations

import difflib
import os

DEFAULT_PROFILE = "default"


class ProfileError(RuntimeError):
    """A configured profile does not exist. Message is meant for a human."""


def profiles_dir(home=None):
    if home:
        return home
    env = os.environ.get("HERMES_HOME")
    if env:
        return os.path.join(env, "profiles")
    return os.path.join(os.path.expanduser("~"), ".hermes", "profiles")


def available_profiles(home=None):
    """Profile names on disk. The implicit default profile is always valid."""
    names = {DEFAULT_PROFILE}
    directory = profiles_dir(home)
    try:
        for entry in os.listdir(directory):
            if os.path.isdir(os.path.join(directory, entry)) and not entry.startswith("."):
                names.add(entry)
    except OSError:
        pass
    return sorted(names)


def validate_profile(name, home=None):
    """Return the normalised name, or raise ProfileError listing what is valid."""
    if not name:
        return ""
    candidate = name.strip().lower()
    if not candidate:
        return ""
    names = available_profiles(home)
    if candidate in names:
        return candidate
    close = difflib.get_close_matches(candidate, names, n=3, cutoff=0.5)
    hint = ""
    if close:
        hint = " Did you mean %s?" % (" or ".join(repr(c) for c in close))
    raise ProfileError(
        "unknown agent profile %r.%s Available profiles: %s. Leave LINEAR_AGENT_PROFILE "
        "empty to use the gateway's default agent." % (candidate, hint, ", ".join(names))
    )
