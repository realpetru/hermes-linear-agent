"""Dashboard API for choosing which Hermes agent answers Linear.

Routes are mounted at ``/api/plugins/linear-agent/``.

Deliberately a thin HTTP skin over the code the CLI already uses:

* profiles come from ``linear_hermes.profiles``
* the choice is written by ``linear_hermes.settings`` — the same 0600
  ``plugin-data/linear-agent/settings.json`` the CLI writes

so the tab and ``hermes linear-agent agent`` can never disagree about either.

FastAPI is optional at import time: the pure helpers below are unit-tested without
it, and the router is built only when the host provides FastAPI.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from linear_hermes.plugin_api import bridge_status  # noqa: E402  (path set above)
from linear_hermes.profiles import ProfileError, available_profiles, validate_profile  # noqa: E402
from linear_hermes.settings import (  # noqa: E402
    env_shadows_profile,
    set_stored_profile,
    settings_path,
    stored_profile,
)

try:  # the host dashboard ships FastAPI; the standalone bridge and tests may not
    from fastapi import APIRouter, HTTPException

    router = APIRouter()
except Exception:  # noqa: BLE001
    APIRouter = None  # type: ignore
    HTTPException = None  # type: ignore
    router = None


def active_profile() -> str:
    """env LINEAR_AGENT_PROFILE beats the stored setting, exactly like the CLI."""
    env = (os.environ.get("LINEAR_AGENT_PROFILE") or "").strip().lower()
    if env:
        return env
    return stored_profile()


def bridge_url() -> str:
    return (
        os.environ.get("LINEAR_AGENT_BRIDGE_URL") or "http://127.0.0.1:8650"
    ).rstrip("/")


def bridge_state(url: str | None = None) -> dict:
    """Never raises: a dead bridge is a state, not an error."""
    probe = bridge_status(url or bridge_url())
    reachable = bool(probe.get("reachable"))
    return {
        "reachable": reachable,
        "version": str(probe.get("version") or ""),
        "sessions_handled": int(probe.get("sessions_handled") or 0),
        "last_error": str(probe.get("last_error") or ""),
        "error": "" if reachable else str(probe.get("error") or "bridge unreachable"),
        "url": probe.get("url") or bridge_url(),
    }


def build_state(url: str | None = None) -> dict:
    """The single document the page renders from."""
    return {
        "active": active_profile(),
        "profiles": available_profiles(),
        "bridge": bridge_state(url),
        "settings_path": settings_path(),
        "env_override": env_shadows_profile(),
    }


def apply_profile(profile) -> dict:
    """Validate and persist a choice. Raises ValueError with a human message."""
    if profile is None:
        raise ValueError("a profile is required; send an empty string to use the default")
    if not isinstance(profile, str):
        raise ValueError("profile must be a string")
    try:
        name = validate_profile(profile)
    except ProfileError as exc:
        raise ValueError(str(exc)) from exc
    set_stored_profile(name)
    return build_state()


if router is not None:  # pragma: no cover - exercised through the HTTP tests

    @router.get("/state")
    def get_state() -> dict:
        return build_state()

    @router.post("/agent")
    def set_agent(payload: dict | None = None) -> dict:
        body = payload or {}
        if "profile" not in body:
            raise HTTPException(status_code=400, detail="send {\"profile\": \"<name>\"}")
        try:
            return apply_profile(body.get("profile"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
