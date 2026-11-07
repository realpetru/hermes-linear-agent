"""Public plugin surface: tools, bundled skill, CLI subcommand.

Imports only the standard library plus this plugin's own modules, which keeps
`hermes plugins validate` happy (catalog rule 9: public surfaces only).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from pathlib import Path

from .secrets import read_setting

logger = logging.getLogger(__name__)

TOOLSET = "linear_agent"
DEFAULT_BRIDGE = "http://127.0.0.1:8650"

STATUS_SCHEMA = {
    "name": "linear_agent_status",
    "description": (
        "Report the health of the local Linear agent bridge: whether it is reachable, "
        "how many agent sessions are running, how many deliveries have been claimed, "
        "and what configuration is missing. Use it when a Linear mention produced no "
        "reply or before telling a user the integration is broken."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}

AUTHORIZE_SCHEMA = {
    "name": "linear_agent_authorize_url",
    "description": (
        "Build the Linear OAuth install URL for this agent. Opening it as a workspace "
        "admin installs the app with actor=app so the agent appears in the assign and "
        "@mention menus. Returns the URL plus a checklist of anything still missing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "description": "Optional CSRF state value echoed back on redirect.",
            }
        },
        "additionalProperties": False,
    },
}


def _bridge_url(ctx=None):
    env = os.environ.get("LINEAR_AGENT_BRIDGE_URL")
    if env:
        return env.rstrip("/")
    if ctx is not None:
        try:
            settings = ctx.get_config() or {}
            if isinstance(settings, dict) and settings.get("bridge_url"):
                return str(settings["bridge_url"]).rstrip("/")
        except Exception:  # noqa: BLE001 - config is best effort
            pass
    return DEFAULT_BRIDGE


def bridge_status(url, timeout=3.0):
    """Probe the bridge. Returns a dict that is always JSON-serialisable."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/status", timeout=timeout) as r:
            payload = json.loads(r.read().decode("utf-8"))
        payload["reachable"] = True
        return payload
    except Exception as exc:  # noqa: BLE001 - report, never raise at the model
        return {"reachable": False, "url": url, "error": "%s: %s" % (type(exc).__name__, exc)}


def _active_profile():
    """env LINEAR_AGENT_PROFILE beats the persisted setting; "" means gateway default."""
    env = (os.environ.get("LINEAR_AGENT_PROFILE") or "").strip().lower()
    if env:
        return env
    from .settings import stored_profile

    return stored_profile()


def _agent_block():
    """Which agent answers, and what else is choosable."""
    from .profiles import ProfileError, available_profiles, validate_profile

    configured = _active_profile()
    block = {
        "agent_profile": configured or "(gateway default)",
        "available_profiles": available_profiles(),
    }
    if configured:
        try:
            validate_profile(configured)
            block["agent_profile_valid"] = True
        except ProfileError as exc:
            block["agent_profile_valid"] = False
            block["agent_profile_error"] = str(exc)
    routing = os.environ.get("LINEAR_AGENT_ROUTING", "").strip()
    block["routing"] = routing or "(none)"
    return block


def tool_status(ctx=None, **_kwargs):
    url = _bridge_url(ctx)
    result = bridge_status(url)
    result.update(_agent_block())
    missing = [
        name
        for name in ("LINEAR_CLIENT_ID", "LINEAR_CLIENT_SECRET", "LINEAR_WEBHOOK_SECRET")
        if not read_setting(name)
    ]
    result["missing_env"] = missing
    if not missing and result.get("reachable"):
        result["verdict"] = "configured and reachable"
    elif not result.get("reachable"):
        result["verdict"] = "bridge not reachable - is it running? try `hermes linear-agent serve`"
    else:
        result["verdict"] = "bridge reachable but credentials missing"
    return json.dumps(result, indent=2)


def tool_authorize_url(ctx=None, state="", **_kwargs):
    client_id = read_setting("LINEAR_CLIENT_ID", "")
    if not client_id:
        return json.dumps({
            "ok": False,
            "error": "LINEAR_CLIENT_ID is not set",
            "next_step": "Create the OAuth app in Linear, then set LINEAR_CLIENT_ID.",
        }, indent=2)
    # LINEAR_REDIRECT_URI is what the token exchange sends, so it must be what the
    # authorize request asks for; otherwise installs die with redirect_uri mismatch.
    public = os.environ.get("LINEAR_AGENT_PUBLIC_URL", "").rstrip("/")
    redirect = os.environ.get("LINEAR_REDIRECT_URI") or ((public or _bridge_url(ctx)) + "/oauth/callback")
    from .oauth import authorize_url, make_state

    if not state:
        state = make_state(read_setting("LINEAR_WEBHOOK_SECRET", ""))
    url = authorize_url(client_id, redirect, state=state)
    return json.dumps({
        "ok": True,
        "authorize_url": url,
        "redirect_uri": redirect,
        "scope": "read,write,app:assignable,app:mentionable",
        "actor": "app",
        "note": "Open as a workspace admin. Must be HTTPS-reachable for Linear to redirect.",
    }, indent=2)


def _setup_cli(parser):
    sub = parser.add_subparsers(dest="linear_agent_command")
    serve = sub.add_parser("serve", help="Run the Linear agent bridge")
    serve.add_argument("--bind", default=os.environ.get("BIND", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8650")))
    status = sub.add_parser("status", help="Print bridge status as JSON")
    status.add_argument("--url", default=os.environ.get("LINEAR_AGENT_BRIDGE_URL", DEFAULT_BRIDGE))
    authorize = sub.add_parser("authorize", help="Print the OAuth install URL")
    authorize.add_argument("--state", default="")
    agent = sub.add_parser("agent", help="Show or change which Hermes agent answers")
    agent.add_argument("action", nargs="?", choices=["list", "set"], default=None)
    agent.add_argument("profile", nargs="?", default=None)


def _handle_cli(args):
    command = getattr(args, "linear_agent_command", None)
    if command == "serve":
        os.environ.setdefault("BIND", args.bind)
        os.environ["PORT"] = str(args.port)
        from .__main__ import main as serve_main

        serve_main()
        return 0
    if command == "status":
        print(json.dumps(bridge_status(args.url), indent=2))
        return 0
    if command == "authorize":
        print(tool_authorize_url(state=args.state))
        return 0
    if command == "agent":
        action = getattr(args, "action", None)
        if action == "list":
            return _agent_list()
        if action == "set":
            return _agent_set(getattr(args, "profile", None))
        block = _agent_block()
        active = block["agent_profile"]
        print("which agent answers Linear: %s" % active)
        if block.get("agent_profile_valid") is False:
            print("  PROBLEM: %s" % block.get("agent_profile_error", "invalid profile"))
        print("available profiles:")
        for name in block["available_profiles"]:
            print("  %s %s" % ("*" if name == active else " ", name))
        if block["routing"] != "(none)":
            print("label routing: %s" % block["routing"])
        print("\nswitch with:  hermes linear-agent agent set <profile>")
        print("label any Linear issue with a profile name (cody, appy, ...) to route it there")
        return 0
    print("usage: hermes linear-agent {serve|status|authorize|agent}")
    return 2


def tool_handler(fn, ctx):
    """The registry dispatches ``entry.handler(args, **kwargs)``.

    A ``lambda **kwargs`` here raises TypeError on every call, which is exactly the
    bug the fake ctx in ``hermes plugins validate`` cannot see.
    """

    def handler(args=None, **kwargs):
        merged = dict(args) if isinstance(args, dict) else {}
        merged.update(kwargs)
        return fn(ctx, **merged)

    return handler


def _skill_path():
    return Path(__file__).resolve().parents[2] / "skills" / "linear-agent" / "SKILL.md"


class _CliParser(__import__("argparse").ArgumentParser):
    pass


def main_for_tests(argv):
    """Parse argv exactly as `hermes linear-agent ...` would, then run it."""
    import argparse

    parser = argparse.ArgumentParser(prog="hermes linear-agent", add_help=False)
    _setup_cli(parser)
    return _handle_cli(parser.parse_args(list(argv)))


def _agent_list():
    from .profiles import available_profiles

    active = _active_profile()
    names = available_profiles()
    for name in names:
        print("%s %s" % ("*" if name == active else " ", name))
    if not active:
        print("\n(no profile set - the gateway default answers)")
    if os.environ.get("LINEAR_AGENT_PROFILE"):
        print("\nnote: LINEAR_AGENT_PROFILE is set in the environment and wins")
    return 0


def _agent_set(profile):
    from .profiles import ProfileError, validate_profile
    from .settings import env_shadows_profile, set_stored_profile, settings_path

    if not profile:
        print("usage: hermes linear-agent agent set <profile>")
        return 2
    try:
        name = validate_profile(profile)
    except ProfileError as exc:
        print(str(exc))
        return 2
    path = set_stored_profile(name)
    print("agent set to %s" % (name or "(gateway default)"))
    print("saved in %s" % path)
    print("takes effect on the next Linear issue - no restart needed")
    if env_shadows_profile():
        print("WARNING: LINEAR_AGENT_PROFILE is set in the environment and overrides this")
    return 0


def register_plugin_surface(ctx):
    """Wire everything through the public API. No core patching.

    Each step is isolated: a failure in one surface must not stop the others from
    registering (a broken skill registration once hid the entire CLI).
    """
    steps = (
        ("tools", lambda: (
            ctx.register_tool(
                name="linear_agent_status", toolset=TOOLSET,
                schema=STATUS_SCHEMA, handler=tool_handler(tool_status, ctx),
            ),
            ctx.register_tool(
                name="linear_agent_authorize_url", toolset=TOOLSET,
                schema=AUTHORIZE_SCHEMA, handler=tool_handler(tool_authorize_url, ctx),
            ),
        )),
        # register_skill takes a PATH, not the file contents.
        ("skill", lambda: ctx.register_skill("linear-agent", _skill_path())),
        ("cli", lambda: ctx.register_cli_command(
            name="linear-agent",
            help="Run and inspect the Linear agent bridge",
            setup_fn=_setup_cli,
            handler_fn=_handle_cli,
        )),
    )
    for label, step in steps:
        try:
            step()
        except Exception:  # noqa: BLE001 - one broken surface must not sink the rest
            logger.exception("linear-agent: failed to register %s", label)
