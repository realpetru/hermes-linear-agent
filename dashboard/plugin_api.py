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
from typing import Any, Dict, Optional
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from linear_hermes.plugin_api import bridge_status, setup_state  # noqa: E402  (path set above)
from linear_hermes.profiles import ProfileError, available_profiles, validate_profile  # noqa: E402
from linear_hermes.settings import (  # noqa: E402
    env_shadows_profile,
    read_settings,
    set_stored_profile,
    set_stored_routing,
    settings_path,
    stored_profile,
    stored_routing,
    write_settings,
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


def effective_routing() -> dict:
    """THE routing map in force: LINEAR_AGENT_ROUTING (JSON) beats the settings file."""
    raw = (os.environ.get("LINEAR_AGENT_ROUTING") or "").strip()
    if raw:
        import json

        try:
            parsed = json.loads(raw)
        except ValueError:
            return stored_routing()
        if isinstance(parsed, dict):
            return {
                str(k).strip().lower(): str(v).strip().lower()
                for k, v in parsed.items()
                if str(k).strip() and str(v).strip()
            }
        return stored_routing()
    return stored_routing()


def routing_from_env() -> bool:
    return bool((os.environ.get("LINEAR_AGENT_ROUTING") or "").strip())


def apply_routing(mapping) -> dict:
    """Validate every target, then persist. Raises ValueError with a human message."""
    if mapping is None:
        raise ValueError('send {"routing": {"<label>": "<profile>"}}')
    if not isinstance(mapping, dict):
        raise ValueError("routing must be an object of label -> profile")
    clean = {}
    for label, profile in mapping.items():
        if not isinstance(label, str) or not isinstance(profile, str):
            raise ValueError('routing entries must be text: {"label": "profile"}')
        label = label.strip()
        if not label:
            raise ValueError("routing entries need a label")
        if not profile.strip():
            raise ValueError(
                "routing entries need a profile — the agent that label should go to"
            )
        try:
            target = validate_profile(profile)
        except ProfileError as exc:
            raise ValueError(str(exc)) from exc
        clean[label.lower()] = target
    try:
        set_stored_routing(clean)
    except OSError as exc:
        raise ValueError("could not write %s: %s" % (settings_path(), exc.strerror or exc)) from exc
    return build_state()


class SettingsConflict(ValueError):
    """Raised when an env var would shadow the write, so saving would be a lie."""


MAX_ROUTING_ENTRIES = 50
MAX_LABEL_LENGTH = 50


def normalise_routing(mapping) -> dict:
    """Validate a routing map. Raises ValueError with a message the page can show."""
    if mapping is None:
        return {}
    if not isinstance(mapping, dict):
        raise ValueError("routing must be an object of label -> profile")
    if len(mapping) > MAX_ROUTING_ENTRIES:
        raise ValueError("too many label rules (max %d)" % MAX_ROUTING_ENTRIES)
    clean = {}
    for raw_label, raw_profile in mapping.items():
        if not isinstance(raw_label, str) or not isinstance(raw_profile, str):
            raise ValueError('routing entries must be text: {"label": "profile"}')
        label = raw_label.strip().lower()
        if not label:
            raise ValueError("routing entries need a label")
        if len(label) > MAX_LABEL_LENGTH:
            raise ValueError("keep label names under %d characters" % MAX_LABEL_LENGTH)
        if label in clean:
            raise ValueError('\u201c%s\u201d is already routed.' % label)
        if not raw_profile.strip():
            raise ValueError("routing entries need a profile — the agent that label should go to")
        try:
            clean[label] = validate_profile(raw_profile)
        except ProfileError as exc:
            raise ValueError(str(exc)) from exc
    return clean


def apply_settings(payload) -> dict:
    """Write agent_profile and routing in ONE operation, so a save is never half-true."""
    if not isinstance(payload, dict):
        raise ValueError('send {"agent_profile": "<name>", "routing": {"label": "profile"}}')
    has_agent = "agent_profile" in payload
    has_routing = "routing" in payload
    if not (has_agent or has_routing):
        raise ValueError('send at least one of "agent_profile" or "routing"')

    if has_agent and env_shadows_profile():
        raise SettingsConflict(
            "LINEAR_AGENT_PROFILE is set by the deployment and takes precedence over "
            "anything saved here."
        )
    if has_routing and routing_from_env():
        raise SettingsConflict(
            "LINEAR_AGENT_ROUTING is set by the deployment. Rules saved here are "
            "ignored until it is removed."
        )

    profile = None
    if has_agent:
        try:
            profile = validate_profile(payload.get("agent_profile") or "")
        except ProfileError as exc:
            raise ValueError(str(exc)) from exc
    routing = normalise_routing(payload.get("routing")) if has_routing else None

    values = read_settings()
    if profile is not None:
        values["agent_profile"] = profile
    if routing is not None:
        values["routing"] = routing
    try:
        write_settings(values)
    except OSError as exc:
        raise ValueError("could not write %s: %s" % (settings_path(), exc.strerror or exc)) from exc
    return build_state()


def bridge_url() -> str:
    return (
        os.environ.get("LINEAR_AGENT_BRIDGE_URL") or "http://127.0.0.1:8650"
    ).rstrip("/")


def bridge_state(url: Optional[str] = None) -> Dict[str, Any]:
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


def build_state(url: Optional[str] = None) -> Dict[str, Any]:
    """The single document the page renders from."""
    return {
        "active": active_profile(),
        "profiles": available_profiles(),
        "bridge": bridge_state(url),
        "settings_path": settings_path(),
        "env_override": env_shadows_profile(),
        "routing": effective_routing(),
        "routing_from_env": routing_from_env(),
        "setup": setup_state(),
        "env_routing": routing_from_env(),
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
    try:
        set_stored_profile(name)
    except OSError as exc:
        # An unwritable settings directory is the operator's problem, and the page
        # should say so in words rather than render a stack trace.
        raise ValueError(
            "could not write %s: %s" % (settings_path(), exc.strerror or exc)
        ) from exc
    return build_state()


if router is not None:  # pragma: no cover - exercised through the HTTP tests

    @router.get("/state")
    def get_state() -> dict:
        return build_state()

    @router.post("/settings")
    def set_settings(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            return apply_settings(payload or {})
        except SettingsConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/routing")
    def set_routing(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body = payload or {}
        if "routing" not in body:
            raise HTTPException(status_code=400, detail='send {"routing": {"label": "profile"}}')
        try:
            return apply_routing(body.get("routing"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/agent")
    def set_agent(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body = payload or {}
        if "profile" not in body:
            raise HTTPException(status_code=400, detail="send {\"profile\": \"<name>\"}")
        try:
            return apply_profile(body.get("profile"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
