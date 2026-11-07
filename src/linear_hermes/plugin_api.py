"""Public plugin surface: tools, bundled skill, CLI subcommand.

Imports only the standard library plus this plugin's own modules, which keeps
`hermes plugins validate` happy (catalog rule 9: public surfaces only).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request

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


def tool_status(ctx=None, **_kwargs):
    url = _bridge_url(ctx)
    result = bridge_status(url)
    missing = [
        name
        for name in ("LINEAR_CLIENT_ID", "LINEAR_CLIENT_SECRET", "LINEAR_WEBHOOK_SECRET")
        if not os.environ.get(name)
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
    client_id = os.environ.get("LINEAR_CLIENT_ID", "")
    if not client_id:
        return json.dumps({
            "ok": False,
            "error": "LINEAR_CLIENT_ID is not set",
            "next_step": "Create the OAuth app in Linear, then set LINEAR_CLIENT_ID.",
        }, indent=2)
    public = os.environ.get("LINEAR_AGENT_PUBLIC_URL", "").rstrip("/")
    redirect = (public or _bridge_url(ctx)) + "/oauth/callback"
    from .oauth import authorize_url

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
    print("usage: hermes linear-agent {serve|status|authorize}")
    return 2


def register_plugin_surface(ctx):
    """Wire everything through the public API. No core patching."""
    ctx.register_tool(
        name="linear_agent_status",
        toolset=TOOLSET,
        schema=STATUS_SCHEMA,
        handler=lambda **kwargs: tool_status(ctx, **kwargs),
    )
    ctx.register_tool(
        name="linear_agent_authorize_url",
        toolset=TOOLSET,
        schema=AUTHORIZE_SCHEMA,
        handler=lambda **kwargs: tool_authorize_url(ctx, **kwargs),
    )

    skill_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "skills", "linear-agent", "SKILL.md")
    if os.path.exists(skill_path):
        with open(skill_path, encoding="utf-8") as fh:
            ctx.register_skill("linear-agent", fh.read())

    ctx.register_cli_command(
        name="linear-agent",
        help="Run and inspect the Linear agent bridge",
        setup_fn=_setup_cli,
        handler_fn=_handle_cli,
    )
