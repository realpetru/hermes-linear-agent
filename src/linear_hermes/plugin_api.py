"""Public plugin surface: tools, bundled skill, CLI subcommand.

Imports only the standard library plus this plugin's own modules, which keeps
`hermes plugins validate` happy (catalog rule 9: public surfaces only).
"""
from __future__ import annotations

import json
import logging
import os
import sys
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


def _config_value(ctx, key):
    """One value from the plugin's `config_schema`, or "".

    The manifest declares bridge_url, public_url, agent_profile and routing. A settings
    screen that writes fields nothing reads is worse than no settings screen, so every one
    of them is consulted below.
    """
    if ctx is None:
        return ""
    try:
        settings = ctx.get_config() or {}
    except Exception:  # noqa: BLE001 - config is best effort
        return ""
    if not isinstance(settings, dict):
        return ""
    value = settings.get(key)
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return value
    return str(value).strip()


def _bridge_url(ctx=None):
    env = (read_setting("LINEAR_AGENT_BRIDGE_URL", "") or "").strip()
    if env:
        return env.rstrip("/")
    configured = _config_value(ctx, "bridge_url")
    if configured:
        return str(configured).rstrip("/")
    return DEFAULT_BRIDGE


def bridge_status(url=None, timeout=3.0):
    """Probe the bridge. Returns a dict that is always JSON-serialisable."""
    url = url or (read_setting("LINEAR_AGENT_BRIDGE_URL", "") or "").strip() or DEFAULT_BRIDGE
    try:
        request = urllib.request.Request(url.rstrip("/") + "/status")
        # Same reason as the doctor probes: a CDN in front of a remote bridge
        # answers 403 to the default Python-urllib agent.
        request.add_header("User-Agent", user_agent())
        token = (read_setting("STATUS_TOKEN") or "").strip()
        if token:
            request.add_header("Authorization", "Bearer " + token)
        with urllib.request.urlopen(request, timeout=timeout) as r:
            payload = json.loads(r.read().decode("utf-8"))
        payload["reachable"] = True
        return payload
    except Exception as exc:  # noqa: BLE001 - report, never raise at the model
        return {"reachable": False, "url": url, "error": "%s: %s" % (type(exc).__name__, exc)}


def _agent_block(ctx=None):
    """Which agent answers, and what else is choosable."""
    from .profiles import ProfileError, available_profiles, validate_profile

    configured = _active_profile(ctx)
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
    routing = (read_setting("LINEAR_AGENT_ROUTING", "") or "").strip()
    if not routing:
        configured = _config_value(ctx, "routing")
        if isinstance(configured, dict) and configured:
            routing = json.dumps(configured, sort_keys=True)
        elif isinstance(configured, str):
            routing = configured
    block["routing"] = routing or "(none)"
    return block


def tool_status(ctx=None, **_kwargs):
    url = _bridge_url(ctx)
    result = bridge_status(url)
    result.update(_agent_block(ctx))
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
    # The link is signed with the state key — the one `setup` generates for exactly this —
    # and only falls back to the webhook secret when no state key exists. Signing with the
    # webhook secret while the server verified the state key produced links that died with
    # "Install link expired" immediately after a successful setup.
    from .settings import stored_state_key

    state_key = str(
        read_setting("LINEAR_STATE_KEY", "")
        or stored_state_key()
        or read_setting("LINEAR_WEBHOOK_SECRET", "")
        or ""
    )
    if not state_key:
        return json.dumps({
            "ok": False,
            "error": "no install-link key",
            "why": "the callback verifies a signed state value; without LINEAR_STATE_KEY (or "
                   "LINEAR_WEBHOOK_SECRET to fall back on) any link printed now would be "
                   "rejected as \"Install link expired\"",
            "next_step": "Run `hermes linear-agent setup` — it generates LINEAR_STATE_KEY.",
        }, indent=2)
    # LINEAR_REDIRECT_URI is what the token exchange sends, so it must be what the
    # authorize request asks for; otherwise installs die with redirect_uri mismatch.
    # There is deliberately no fall-back to the internal bridge URL: Linear has never
    # heard of that address, and it is 404-shaped rather than obviously wrong.
    urls = _urls(ctx)
    redirect = urls["redirect_uri"]
    if not redirect:
        return json.dumps({
            "ok": False,
            "error": "no OAuth callback URL is configured",
            "next_step": "Set LINEAR_AGENT_PUBLIC_URL (e.g. https://your-host.example) or "
                         "LINEAR_REDIRECT_URI, then re-run.",
        }, indent=2)
    from .oauth import authorize_url, make_state

    if not state:
        state = make_state(state_key)
    url = authorize_url(client_id, redirect, state=state)
    return json.dumps({
        "ok": True,
        "authorize_url": url,
        "redirect_uri": redirect,
        "scope": "read,write,app:assignable,app:mentionable",
        "actor": "app",
        "note": "Open as a workspace admin. Must be HTTPS-reachable for Linear to redirect.",
    }, indent=2)


CREDENTIALS = ("LINEAR_CLIENT_ID", "LINEAR_CLIENT_SECRET", "LINEAR_WEBHOOK_SECRET")


def _version():
    """The plugin's own manifest version — the one place it cannot drift from."""
    try:
        import re
        from pathlib import Path

        manifest = Path(__file__).resolve().parents[2] / "plugin.yaml"
        match = re.search(r"^version:\s*(\S+)", manifest.read_text(encoding="utf-8"), re.M)
        return match.group(1) if match else "unknown"
    except Exception:  # noqa: BLE001 - never let a version lookup break a command
        return "unknown"


def _present(name):
    return bool(str(read_setting(name, "") or "").strip())


VIEW_KEYS = (
    "LINEAR_AGENT_PUBLIC_URL",
    "LINEAR_REDIRECT_URI",
    "LINEAR_AGENT_BRIDGE_URL",
    "LINEAR_CLIENT_ID",
    "LINEAR_CLIENT_SECRET",
    "LINEAR_STATE_KEY",
    "LINEAR_WEBHOOK_SECRET",
    "LINEAR_TOKEN_PATH",
    "LINEAR_API_KEY",
    "HERMES_GATEWAY_URL",
    "HERMES_GATEWAY_TOKEN",
)


def _env_view():
    """What this plugin actually resolves — profile scope first, never a ``.env`` read.

    Hermes owns the profile's credential file and exposes it through ``agent.secret_scope``;
    catalog rule 11 forbids reading that file directly. The two non-secret values setup
    remembers (public URL, install-link key) come from the plugin's own settings.
    """
    from .settings import stored_public_url, stored_state_key

    env = {name: str(read_setting(name, "") or "") for name in VIEW_KEYS}
    if not env["LINEAR_AGENT_PUBLIC_URL"]:
        env["LINEAR_AGENT_PUBLIC_URL"] = stored_public_url()
    if not env["LINEAR_STATE_KEY"]:
        env["LINEAR_STATE_KEY"] = stored_state_key()
    return env


def _urls(ctx=None):
    """The two URLs a user must paste into the Linear app, as the code sees them."""
    env = _env_view()
    public = (
        env.get("LINEAR_AGENT_PUBLIC_URL")
        or str(_config_value(ctx, "public_url") or "")
        or ""
    ).rstrip("/")
    redirect = (env.get("LINEAR_REDIRECT_URI") or "") or (
        (public + "/oauth/callback") if public else ""
    )
    return {
        "public_url": public,
        "webhook_url": (public + "/webhook") if public else "",
        "redirect_uri": redirect,
    }


def _token_state():
    """(path, record) for the app token, without ever printing its value."""
    from .config import default_data_dir
    from .oauth import load_tokens

    path = (read_setting("LINEAR_TOKEN_PATH", "") or "").strip() or os.path.join(
        default_data_dir(), "linear-tokens.json")
    try:
        record = load_tokens(path)
    except Exception:  # noqa: BLE001 - a corrupt file is a finding, not a crash
        record = None
    return path, record


def _token_facts():
    path, record = _token_state()
    facts = {"path": path, "present": bool(record), "mode": "", "expires_at": ""}
    if record:
        facts["expires_at"] = str(record.get("expires_at") or "")
        try:
            facts["mode"] = oct(os.stat(path).st_mode & 0o777)[2:]
        except OSError:
            facts["mode"] = "?"
    return facts


def _active_profile(ctx=None):
    """scope/env beats the settings file, which beats the manifest's agent_profile; "" = default."""
    env = (read_setting("LINEAR_AGENT_PROFILE", "") or "").strip().lower()
    if env:
        return env
    try:
        from .settings import stored_profile

        stored = stored_profile()
    except Exception:  # noqa: BLE001
        stored = ""
    return stored or str(_config_value(ctx, "agent_profile") or "").strip().lower()


def status_document(url=None, probe=None):
    """Everything needed to answer "is this install working?".

    ``probe`` exists so tests never touch the network; the CLI passes nothing and asks Linear
    about the token, because reporting "ok" for a token Linear answers 401 to is a lie.
    """
    request = probe or _probe
    probe = bridge_status(url)
    missing = [name for name in CREDENTIALS if not _present(name)]
    doc = {
        "service": "linear-agent",
        "version": _version(),
        "status": "degraded" if missing else "ok",
        "token_valid": None,
        "bridge": probe,
        "credentials": {name: ("set" if _present(name) else "MISSING") for name in CREDENTIALS},
        "missing": missing,
        "agent_profile": _active_profile(),
        "urls": _urls(),
        "token": _token_facts(),
        "settings_path": _settings_path_public(),
    }
    path, record = _token_state()
    if record and record.get("access_token"):
        # The audit's point: `status` printed ok for a token Linear answers 401 to. Ask.
        verdict = _linear_viewer(str(record["access_token"]), request)
        doc["token_valid"] = verdict is True
        if verdict is False:
            doc["status"] = "degraded"
        if verdict is None:
            doc["token_unverified"] = "Linear did not answer; the token is present but unproven"
        elif isinstance(verdict, str):
            doc["token_error"] = verdict
            doc["status"] = "degraded"
    if probe.get("reachable"):
        for key in ("sessions_handled", "last_error", "agent_profile"):
            if probe.get(key) not in (None, "", "(gateway default)"):
                doc[key] = probe[key]
    return doc


def _settings_path_public():
    try:
        from .settings import settings_path

        return settings_path()
    except Exception:  # noqa: BLE001
        return ""


def user_agent():
    """A real User-Agent for our own probes.

    The default ``Python-urllib/x`` is refused with 403 by Cloudflare's bot rules, and
    a tunnel in front of the bridge is the normal deployment — without this, doctor
    reports "cannot reach this" on a perfectly healthy install.
    """
    return "linear-agent-doctor/%s" % _version()


def _probe(url, timeout=5, headers=None, data=None, method=None):
    """Small injectable HTTP helper: tests replace it, doctor uses it."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, data=data, method=method)
    given = {k.lower() for k in (headers or {})}
    if "user-agent" not in given:
        request.add_header("User-Agent", user_agent())
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.getcode(), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # a 4xx is a result, not an exception
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - a network failure is a finding
        return 0, str(exc)


def _linear_viewer(access_token, probe):
    """Workspace name for this app token, an error string, or None when unknown."""
    from .linear import API_URL

    body = json.dumps({"query": "query Me { viewer { organization { name } } }"}).encode("utf-8")
    status, text = probe(
        API_URL,
        10,
        headers={"Content-Type": "application/json", "Authorization": access_token},
        data=body,
        method="POST",
    )
    if status == 200:
        try:
            payload = json.loads(text)
        except ValueError:
            return "Linear answered non-JSON; the token is probably valid but unverified"
        viewer = (payload.get("data") or {}).get("viewer") or {}
        name = (viewer.get("organization") or {}).get("name")
        return True if name else "token works but this app is not installed in a workspace"
    if status in (401, 403):
        return "token rejected by Linear (HTTP %s) — re-run: hermes linear-agent authorize" % status
    return None


def doctor(timeout=5, probe=None):
    """One command that proves the install. Returns (checks, ok)."""
    probe = probe or _probe
    from .signature import compute_signature

    checks = []
    secrets = {name: str(read_setting(name, "") or "") for name in CREDENTIALS}
    missing = [name for name in CREDENTIALS if not secrets[name].strip()]
    urls = _urls()
    if missing:
        checks.append(("config", False, "missing: %s" % ", ".join(missing)))
    elif not urls["public_url"]:
        checks.append(("config", False, "LINEAR_AGENT_PUBLIC_URL is not set — Linear cannot reach this"))
    else:
        checks.append(("config", True, "LINEAR_CLIENT_ID, LINEAR_CLIENT_SECRET, LINEAR_WEBHOOK_SECRET set"))

    path, record = _token_state()
    if not record:
        checks.append(("token", False, "no app token at %s — run: hermes linear-agent authorize" % path))
    else:
        verdict = _linear_viewer(str(record.get("access_token") or ""), probe)
        mode = _token_facts()["mode"]
        if verdict is True:
            if mode not in ("600", ""):
                checks.append(("token", False,
                               "app token is valid but the file is mode %s — chmod 600 %s"
                               % (mode, path)))
            else:
                checks.append(("token", True, "app token valid, refreshes automatically"))
        elif verdict is None:
            # "Could not ask" is not "fine": a token nobody verified is not proof of anything.
            checks.append(("token", False,
                           "app token present at %s but Linear did not answer, so it is UNVERIFIED "
                           "— re-run doctor when the network is back" % path))
        else:
            checks.append(("token", False, verdict))

    bridge = bridge_status(None)
    if bridge.get("reachable"):
        running = str(bridge.get("version") or "")
        expected = _version()
        if running and running != expected:
            checks.append(("bridge", False,
                           "%s is running %s but this plugin is %s — restart the bridge "
                           "(`hermes linear-agent serve`) so the deployed code matches"
                           % (bridge.get("url", ""), running, expected)))
        else:
            checks.append(("bridge", True, "%s (%s)" % (bridge.get("url", ""), running or "version unknown")))
    else:
        checks.append((
            "bridge",
            False,
            "not reachable — is `hermes linear-agent serve` running? (%s)" % (bridge.get("error") or "connection refused"),
        ))

    if not urls["public_url"]:
        checks.append(("public", False, "no public URL configured"))
    else:
        status, _body = probe(urls["public_url"] + "/healthz", timeout)
        if status == 200:
            checks.append(("public", True, urls["public_url"] + "/healthz"))
        elif status in (403, 429):
            checks.append((
                "public",
                False,
                "%s/healthz answered %s — blocked in front of the bridge (proxy/CDN bot rule, "
                "e.g. Cloudflare), not by the bridge. The bridge is probably fine: allow "
                "linear-agent-doctor or this path at the edge." % (urls["public_url"], status),
            ))
        elif status == 0:
            checks.append((
                "public",
                False,
                "%s/healthz answered nothing — DNS, tunnel or firewall, not the bridge"
                % urls["public_url"],
            ))
        else:
            checks.append((
                "public",
                False,
                "%s/healthz answered %s — Linear cannot reach this" % (urls["public_url"], status),
            ))

    if urls["webhook_url"] and secrets["LINEAR_WEBHOOK_SECRET"]:
        # A real timestamp: with LINEAR_MAX_SKEW_SECONDS>0 the server rejects a zero one, and
        # doctor then "proved" a webhook that would refuse every delivery.
        import time as _time

        body = json.dumps({"action": "created", "type": "AgentSessionEvent",
                           "webhookTimestamp": int(_time.time() * 1000)}).encode("utf-8")
        signature = compute_signature(secrets["LINEAR_WEBHOOK_SECRET"], body)
        status, _body = probe(
            urls["webhook_url"],
            timeout,
            headers={"Content-Type": "application/json", "Linear-Signature": signature},
            data=body,
            method="POST",
        )
        if status == 200:
            checks.append(("signature", True, "signed test delivery accepted (200)"))
        elif status == 401:
            checks.append(("signature", False, "401 — LINEAR_WEBHOOK_SECRET does not match this webhook"))
        elif status in (403, 429):
            checks.append((
                "signature",
                False,
                "%s — blocked in front of the bridge (proxy/CDN bot rule), not a signature "
                "problem. Allow linear-agent-doctor or the /webhook path at the edge." % status,
            ))
        elif status == 0:
            checks.append(("signature", False, "signed delivery answered nothing — the public host did not respond"))
        else:
            checks.append(("signature", False, "signed delivery answered %s" % status))
    else:
        checks.append(("signature", False, "no public URL or no webhook secret"))

    env = _env_view()
    gateway = (env.get("HERMES_GATEWAY_URL") or read_setting("HERMES_GATEWAY_URL", "http://127.0.0.1:8642") or "").rstrip("/")
    gateway_token = env.get("HERMES_GATEWAY_TOKEN") or read_setting("HERMES_GATEWAY_TOKEN", "") or ""
    if not gateway:
        checks.append(("gateway", False, "HERMES_GATEWAY_URL is not set"))
    else:
        status, _body = probe(
            gateway + "/v1/models",
            timeout,
            headers={"Authorization": "Bearer " + gateway_token} if gateway_token else None,
        )
        if status == 200:
            checks.append(("gateway", True, gateway + "/v1/models"))
        elif status in (401, 403):
            checks.append(("gateway", False, "401 — HERMES_GATEWAY_TOKEN is not API_SERVER_KEY"))
        else:
            checks.append((
                "gateway",
                False,
                "refused at %s — enable the Hermes API server (API_SERVER_ENABLED=true)" % gateway,
            ))

    return checks, all(ok for _name, ok, _detail in checks)


def setup_state():
    """What the install still needs, from local facts only — no network on a tab load."""
    env = _env_view()
    public = (env.get("LINEAR_AGENT_PUBLIC_URL") or "").rstrip("/")
    steps = []

    def add(name, done, detail):
        steps.append({"name": name, "state": "done" if done else "needs_you", "detail": detail})

    add("public_url", bool(public),
        public or "no public URL — pass --public-url, then setup verifies it")
    add("state_key", bool((env.get("LINEAR_STATE_KEY") or "").strip()),
        "install-link key set" if env.get("LINEAR_STATE_KEY") else "generated by setup")
    add("app", bool((env.get("LINEAR_CLIENT_ID") or "") and (env.get("LINEAR_CLIENT_SECRET") or "")),
        "Linear app credentials set" if env.get("LINEAR_CLIENT_ID")
        else "create the app, paste two values")

    bridge = bridge_status(None)
    add("bridge", bool(bridge.get("reachable")),
        bridge.get("url") if bridge.get("reachable") else "not running — setup starts it")

    _path, record = _token_state()
    add("install", bool(record), "app token stored" if record else "install the app in the workspace")

    add("webhook", bool((env.get("LINEAR_WEBHOOK_SECRET") or "").strip()),
        "Linear's signing secret stored" if env.get("LINEAR_WEBHOOK_SECRET")
        else "paste the app's Webhook URL and copy the signing secret Linear shows")

    return {"ok": all(s["state"] == "done" for s in steps), "steps": steps}


def _remembered_setup():
    """The last `setup` run, with no secrets in it by construction."""
    import json as _json

    from .config import default_data_dir

    try:
        with open(os.path.join(default_data_dir(), "setup.json")) as fh:
            return _json.load(fh)
    except Exception:  # noqa: BLE001 - absent or unreadable is the same answer
        return {}


def _setup_cli(parser):
    sub = parser.add_subparsers(dest="linear_agent_command")
    serve = sub.add_parser("serve", help="Run the Linear agent bridge")
    # Defaults are None so .env decides; a flag only overrides when actually given.
    serve.add_argument("--bind", default=None, help="interface to bind (default: BIND, else 127.0.0.1)")
    serve.add_argument("--port", type=int, default=None, help="port (default: PORT, else 8650)")
    status = sub.add_parser("status", help="Show what this install is doing")
    status.add_argument("--url", default=None)
    status.add_argument("--json", action="store_true", help="machine-readable output")
    doctor_cmd = sub.add_parser("doctor", help="Check every part of the install; exits non-zero on failure")
    doctor_cmd.add_argument("--json", action="store_true")
    exchange_cmd = sub.add_parser("exchange", help="Store the app token from a redirect URL or code")
    exchange_cmd.add_argument("code")
    authorize = sub.add_parser("authorize", help="Print the OAuth install URL")
    authorize.add_argument("--state", default="")
    setup_cmd = sub.add_parser("setup", help="Install everything in one command (resumable)")
    setup_cmd.add_argument("--public-url", default=None,
                           help="public HTTPS base URL; skips the tunnel step")
    setup_cmd.add_argument("--yes", action="store_true",
                           help="never prompt; only use values that are already there")
    setup_cmd.add_argument("--json", action="store_true", help="machine-readable checklist")
    agent = sub.add_parser("agent", help="Show or change which Hermes agent answers")
    agent.add_argument("action", nargs="?", choices=["list", "set"], default=None)
    agent.add_argument("profile", nargs="?", default=None)


def _handle_cli(args):
    command = getattr(args, "linear_agent_command", None)
    if command == "serve":
        # Only write what the user actually asked for: forcing defaults here would
        # silently override BIND/PORT from .env (config owns the precedence).
        if getattr(args, "bind", None):
            os.environ["BIND"] = args.bind
        if getattr(args, "port", None):
            os.environ["PORT"] = str(args.port)
        from .config import ConfigError
        from .__main__ import main as serve_main

        try:
            serve_main()
        except (ConfigError, SystemExit) as exc:
            if not isinstance(exc, ConfigError):
                raise
            print("linear-agent is not configured yet: %s" % exc)
            print("")
            for name in CREDENTIALS:
                print("  %-22s %s" % (name, "set" if _present(name) else "missing"))
            print("")
            print("Create the Linear app (docs/INSTALL.md step 2), then either re-run")
            print("  hermes plugins install realpetru/hermes-linear-agent --force")
            print("for masked prompts, or store each value with")
            print("  hermes config set LINEAR_CLIENT_ID <value>   (and LINEAR_CLIENT_SECRET,")
            print("  LINEAR_WEBHOOK_SECRET).")
            return 2
        return 0
    if command == "status":
        doc = status_document(args.url)
        if getattr(args, "json", False):
            print(json.dumps(doc, indent=2))
            return 0
        print("linear-agent %s — %s" % (doc["version"], doc["status"]))
        print("  agent      %s" % (doc.get("agent_profile") or "(gateway default)"))
        bridge = doc["bridge"]
        if bridge.get("reachable"):
            print("  bridge     %s" % bridge.get("url", ""))
        else:
            print("  bridge     NOT REACHABLE (%s)" % (bridge.get("error") or "connection refused"))
        for name, state in doc["credentials"].items():
            print("  %-10s %s" % (name.replace("LINEAR_", "").lower()[:10], state))
        token = doc["token"]
        print("  token      %s" % (token["path"] if token["present"] else "none — run: hermes linear-agent authorize"))
        if doc["urls"]["public_url"]:
            print("  webhook    %s" % doc["urls"]["webhook_url"])
            print("  callback   %s" % doc["urls"]["redirect_uri"])
        else:
            print("  webhook    (LINEAR_AGENT_PUBLIC_URL not set)")
        if doc.get("sessions_handled") is not None:
            print("  sessions   %s handled, last error: %s" % (doc.get("sessions_handled"), doc.get("last_error") or "none"))
        print("")
        print("run `hermes linear-agent doctor` to check every part of the install")
        return 0
    if command == "doctor":
        checks, ok = doctor()
        if getattr(args, "json", False):
            print(json.dumps({
                "ok": ok,
                "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in checks],
            }, indent=2))
        else:
            print("linear-agent %s" % _version())
            print("")
            for name, passed, detail in checks:
                print("  %-10s %-4s %s" % (name, "ok" if passed else "FAIL", detail))
            urls = _urls()
            if urls["public_url"]:
                print("")
                print("  Paste these into the Linear app:")
                print("    Webhook URL     %s" % urls["webhook_url"])
                print("    Callback URL    %s" % urls["redirect_uri"])
                print("    Category        Agent session events")
        return 0 if ok else 1
    if command == "setup":
        from .setup import Setup, render

        # In --json mode the only thing on stdout is the JSON: the human guidance
        # (what a public URL is for, which links to open) goes to stderr instead.
        as_json = getattr(args, "json", False)
        writer = (lambda line="": print(line, file=sys.stderr)) if as_json else print
        setup = Setup(public_url=getattr(args, "public_url", None),
                      yes=getattr(args, "yes", False), out=writer)
        result = setup.run()
        if getattr(args, "json", False):
            print(json.dumps(result, indent=2))
        else:
            code = render(result)
            return code
        return 0 if result["ok"] else 1
    if command == "exchange":
        from .exchange import main as exchange_main

        return exchange_main([args.code])
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
    print("usage: hermes linear-agent {setup|serve|status|authorize|agent}")
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
    if (read_setting("LINEAR_AGENT_PROFILE", "") or "").strip():
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
