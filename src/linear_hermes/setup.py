"""`hermes linear-agent setup` — the whole install in one resumable command.

The install used to be five values pasted into four different places, in an order that
mattered and with no way to tell what was already done. This walks the same ground, asks
for as little as possible, and can be re-run at any point: every step reports ``done``,
``skipped``, ``needs_input`` or ``failed``, and a second run changes nothing.

What it removes:

* the **webhook URL** and the **signing secret** are no longer typed anywhere — the app
  token creates the webhook itself, with a secret this command generates (Linear's
  ``WebhookCreateInput`` accepts a caller-supplied ``secret``);
* the **callback URL** is printed fully formed next to the app-creation link, so it is
  copied rather than composed;
* the **public URL** is discovered by starting a tunnel, when ``cloudflared`` or ``ngrok``
  is present.

Two pastes remain, both unavoidable: the Linear app's client id and client secret.

Nothing here prints a secret. Values are written to ``.env`` at mode 0600.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time

DONE = "done"
SKIPPED = "skipped"
NEEDS_INPUT = "needs_input"
FAILED = "failed"

APP_URL = "https://linear.app/settings/api/applications/new"

WEBHOOKS_QUERY = """
query ExistingWebhooks {
  webhooks {
    nodes { id url enabled }
  }
}
"""

WEBHOOK_CREATE = """
mutation CreateWebhook($input: WebhookCreateInput!) {
  webhookCreate(input: $input) {
    success
    webhook { id url enabled }
  }
}
"""

# AgentSessionEvent is the only event the bridge acts on. Comment and Issue are
# deliberately not subscribed: they would arrive, be ignored, and cost Linear quota.
WEBHOOK_RESOURCES = ["AgentSessionEvent"]

TUNNEL_PATTERN = re.compile(r"https://[a-z0-9-]+\.(?:trycloudflare\.com|ngrok\.io|ngrok-free\.app)")

TUNNEL_HELP = """\
No tunnel found. Linear needs a public HTTPS address for the callback and the webhook,
so install one of these and re-run the same command:

  brew install cloudflared          # then: setup starts the tunnel for you
  brew install ngrok                # or this

Already have a public address (a VPS, a named tunnel, a reverse proxy)? Set it and
setup skips this step entirely:

  hermes linear-agent setup --public-url https://linear.example.com
"""


class SetupError(RuntimeError):
    """A step failed for a reason the user has to act on."""


def _mode_of(path):
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return None


class Setup:
    """Run the install steps in order, with every side effect injectable."""

    def __init__(self, env_path=None, public_url=None, yes=False, interactive=None,
                 tunnel=None, which=None, runner=None, probe=None, sleep=None, out=None,
                 transport=None, timeout_install=300, timeout_bridge=60, timeout_tunnel=45,
                 clock=None, doctor_fn=None):
        from .config import default_env_file, load_dotenv

        self.env_path = env_path or default_env_file()
        self.public_url_flag = (public_url or "").rstrip("/")
        self.yes = yes
        self.interactive = (sys.stdin.isatty() if interactive is None else interactive)
        self.tunnel_choice = tunnel
        self._which = which or shutil.which
        self._runner = runner or subprocess.Popen
        self._probe = probe
        self.sleep = sleep or time.sleep
        self.out = out or print
        self._transport = transport
        self._clock = clock or time.time
        self.timeout_install = timeout_install
        self.timeout_bridge = timeout_bridge
        self.timeout_tunnel = timeout_tunnel
        self._doctor = doctor_fn
        self.env = dict(load_dotenv(self.env_path))
        self.env.update({k: v for k, v in os.environ.items() if k.startswith("LINEAR")})
        self.tunnel_process = None
        self.bridge_process = None

    # ------------------------------------------------------------------ output
    def say(self, line=""):
        self.out(line)

    def _write_env(self, updates):
        """Merge keys into .env atomically, at 0600. Values are never echoed."""
        lines = []
        if os.path.exists(self.env_path):
            with open(self.env_path) as fh:
                lines = fh.read().splitlines()
        seen = set()
        merged = []
        for line in lines:
            stripped = line.strip()
            key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else None
            if key in updates:
                merged.append("%s=%s" % (key, updates[key]))
                seen.add(key)
            else:
                merged.append(line)
        for key, value in updates.items():
            if key not in seen:
                merged.append("%s=%s" % (key, value))
        directory = os.path.dirname(os.path.abspath(self.env_path))
        os.makedirs(directory, exist_ok=True)
        tmp = self.env_path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write("\n".join(merged).rstrip() + "\n")
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600 before content lands
        os.replace(tmp, self.env_path)
        self.env.update(updates)
        for key, value in updates.items():
            os.environ[key] = value
        return sorted(updates)

    def _ask(self, label, secret=False):
        if self.yes or not self.interactive:
            return ""
        try:
            if secret:
                import getpass

                return (getpass.getpass("%s: " % label) or "").strip()
            return input("%s: " % label).strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    # ------------------------------------------------------------------- steps
    def step_public_url(self):
        existing = self.public_url_flag or (self.env.get("LINEAR_AGENT_PUBLIC_URL") or "").rstrip("/")
        if existing:
            self._write_env({"LINEAR_AGENT_PUBLIC_URL": existing})
            return DONE, "using %s" % existing

        binary = self._tunnel_binary()
        if not binary:
            self.say(TUNNEL_HELP)
            return FAILED, "no public URL set and no tunnel installed (cloudflared or ngrok)"

        port = self.env.get("PORT") or "8650"
        try:
            url, process = self._start_tunnel(binary, port)
        except SetupError as exc:
            return FAILED, str(exc)
        self.tunnel_process = process
        self._write_env({"LINEAR_AGENT_PUBLIC_URL": url})
        self.say("  tunnel      %s (quick tunnels hand out a new hostname on every restart)" % url)
        self.say("  to keep one: cloudflared tunnel create hermes && point its DNS at this host")
        return DONE, "%s via %s" % (url, os.path.basename(binary))

    def _tunnel_binary(self):
        if self.tunnel_choice and self._which(self.tunnel_choice):
            return self._which(self.tunnel_choice)
        for name in ("cloudflared", "ngrok"):
            found = self._which(name)
            if found:
                return found
        return None

    def _start_tunnel(self, binary, port):
        name = os.path.basename(binary)
        if name.startswith("ngrok"):
            command = [binary, "http", str(port), "--log=stdout", "--log-format=json"]
        else:
            command = [binary, "tunnel", "--url", "http://127.0.0.1:%s" % port]
        try:
            # The URL is printed on stderr; a tunnel never exits, so we read until it appears.
            process = self._runner(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1)
        except OSError as exc:
            raise SetupError("could not start %s: %s" % (name, exc))
        deadline = self._clock() + self.timeout_tunnel
        stream = getattr(process, "stdout", None)
        while stream is not None and self._clock() < deadline:
            line = stream.readline()
            if not line:
                break
            found = TUNNEL_PATTERN.search(line)
            if found:
                return found.group(0).rstrip("/"), process
        try:
            process.terminate()
        except Exception:  # noqa: BLE001 - best effort on a failed start
            pass
        raise SetupError("%s started but printed no https hostname within %ss"
                         % (name, self.timeout_tunnel))

    def step_app(self):
        client_id = (self.env.get("LINEAR_CLIENT_ID") or "").strip()
        client_secret = (self.env.get("LINEAR_CLIENT_SECRET") or "").strip()
        urls = self._urls()
        if client_id and client_secret:
            return DONE, "client id and secret already set"

        self.say("")
        self.say("  Create the Linear app (one click, then paste two values back here):")
        self.say("    %s" % APP_URL)
        self.say("")
        self.say("    Callback URL   %s" % (urls["redirect_uri"] or "(set a public URL first)"))
        self.say("    Scopes         read, write, app:assignable, app:mentionable")
        self.say("    Webhooks       Agent session events")
        self.say("")
        if not self.interactive or self.yes:
            return NEEDS_INPUT, "create the app at %s, then re-run setup" % APP_URL

        updates = {}
        if not client_id:
            client_id = self._ask("Client ID")
            if not client_id:
                return NEEDS_INPUT, "no client id given"
            updates["LINEAR_CLIENT_ID"] = client_id
        if not client_secret:
            client_secret = self._ask("Client secret", secret=True)
            if not client_secret:
                return NEEDS_INPUT, "no client secret given"
            updates["LINEAR_CLIENT_SECRET"] = client_secret
        if updates:
            self._write_env(updates)
        return DONE, "client id and secret stored in %s" % self.env_path

    def step_secret(self):
        """The webhook secret doubles as the CSRF state key and the bridge's verifier."""
        current = (self.env.get("LINEAR_WEBHOOK_SECRET") or "").strip()
        if current:
            return SKIPPED, "using the existing webhook secret"
        secret = os.urandom(32).hex()
        self._write_env({"LINEAR_WEBHOOK_SECRET": secret})
        mode = _mode_of(self.env_path)
        return DONE, "generated a 32-byte webhook secret (%s, mode %s)" % (self.env_path, mode)

    def step_bridge(self):
        port = self.env.get("PORT") or "8650"
        health = "http://127.0.0.1:%s/healthz" % port
        if self._health(health):
            return SKIPPED, "already answering at %s" % health

        compose = os.path.exists("docker-compose.yml")
        docker = self._which("docker")
        try:
            if compose and docker:
                self._runner([docker, "compose", "up", "-d"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
                started = "docker compose up -d"
            else:
                self.bridge_process = self._runner(
                    [sys.executable, "-m", "linear_hermes"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=self._child_env(),
                )
                started = "python -m linear_hermes (pid %s)" % getattr(self.bridge_process, "pid", "?")
        except OSError as exc:
            return FAILED, "could not start the bridge: %s" % exc

        deadline = self._clock() + self.timeout_bridge
        while self._clock() < deadline:
            if self._health(health):
                return DONE, "%s, answering at %s" % (started, health)
            self.sleep(1)
        return FAILED, ("started %s but %s never answered — check the logs with "
                        "`docker compose logs` or `hermes linear-agent status`" % (started, health))

    def _child_env(self):
        env = dict(os.environ)
        env.update({k: v for k, v in self.env.items() if k.startswith("LINEAR")})
        return env

    def _health(self, url):
        probe = self._probe or self._default_probe
        try:
            status, _body = probe(url, 3)
        except Exception:  # noqa: BLE001 - connection refused is the normal first answer
            return False
        return status == 200

    @staticmethod
    def _default_probe(url, timeout):
        import urllib.error
        import urllib.request

        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, b""
        except Exception:  # noqa: BLE001
            return 0, b""

    def step_install(self):
        path, record = self._token_state()
        if record:
            return SKIPPED, "app token already stored at %s" % path

        from .oauth import authorize_url, make_state

        client_id = (self.env.get("LINEAR_CLIENT_ID") or "").strip()
        if not client_id:
            return NEEDS_INPUT, "no client id yet — finish the app step first"
        state = make_state((self.env.get("LINEAR_WEBHOOK_SECRET") or "").strip())
        url = authorize_url(client_id, self._urls()["redirect_uri"], state=state)
        self.say("")
        self.say("  Install the app in your workspace (as an admin):")
        self.say("    %s" % url)
        self.say("")

        deadline = self._clock() + self.timeout_install
        while self._clock() < deadline:
            _path, fresh = self._token_state()
            if fresh:
                return DONE, "app token stored at %s" % path
            self.sleep(2)
        return self._install_fallback(path)

    def _install_fallback(self, path):
        """If the callback could not be reached, finish with a pasted redirect URL."""
        if self.yes or not self.interactive:
            return NEEDS_INPUT, "open the authorize URL above, then re-run setup"
        pasted = self._ask("Callback did not arrive. Paste the full redirect URL")
        if not pasted:
            return NEEDS_INPUT, "no redirect URL given"
        from .exchange import extract_code
        from .linear import urllib_transport
        from .oauth import exchange_code, save_tokens

        code = extract_code(pasted)
        if not code:
            return FAILED, "no ?code= found in that URL"
        try:
            record = exchange_code(
                (self.env.get("LINEAR_CLIENT_ID") or "").strip(),
                (self.env.get("LINEAR_CLIENT_SECRET") or "").strip(),
                code, self._urls()["redirect_uri"],
                self._transport or urllib_transport,
            )
        except Exception as exc:  # noqa: BLE001 - the message is the product here
            return FAILED, "token exchange failed: %s" % exc
        save_tokens(path, record)
        return DONE, "app token stored at %s (via the pasted redirect)" % path

    def step_webhook(self):
        path, record = self._token_state()
        if not record:
            return NEEDS_INPUT, "no app token yet — finish the install step first"
        token = str(record.get("access_token") or "")
        urls = self._urls()
        target = urls["webhook_url"]
        if not target:
            return NEEDS_INPUT, "no public URL — Linear has nowhere to send events"

        status, payload = self._graphql(token, WEBHOOKS_QUERY, {})
        if status != 200:
            return FAILED, self._scope_message(status, "list webhooks")
        existing = ((payload.get("data") or {}).get("webhooks") or {}).get("nodes") or []
        for node in existing:
            if str(node.get("url") or "").rstrip("/") == target:
                return SKIPPED, "a webhook already points at %s" % target

        secret = (self.env.get("LINEAR_WEBHOOK_SECRET") or "").strip()
        if not secret:
            return NEEDS_INPUT, "no webhook secret — re-run setup from the top"

        status, payload = self._graphql(token, WEBHOOK_CREATE, {
            "input": {
                "url": target,
                "secret": secret,
                "resourceTypes": WEBHOOK_RESOURCES,
                "allPublicTeams": True,
                "enabled": True,
                "label": "Hermes agent",
            }
        })
        if status != 200:
            return FAILED, self._scope_message(status, "create the webhook")
        data = (payload.get("data") or {}).get("webhookCreate") or {}
        if data.get("success") is False or not data.get("webhook"):
            errors = payload.get("errors") or []
            return FAILED, "Linear refused the webhook: %s" % (errors[:1] or "no reason given")
        webhook_id = (data.get("webhook") or {}).get("id")
        return DONE, "created webhook %s -> %s" % (webhook_id, target)

    def _scope_message(self, status, doing):
        if status in (401, 403):
            return ("the app token lacks write scope, so it cannot %s. Recreate the Linear app "
                    "with read,write and re-run setup." % doing)
        return "Linear returned HTTP %s while trying to %s" % (status, doing)

    def step_doctor(self):
        if self._doctor is not None:
            checks, ok = self._doctor()
        else:
            from .plugin_api import doctor

            checks, ok = doctor()
        for name, passed, detail in checks:
            self.say("  %-10s %-4s %s" % (name, "ok" if passed else "FAIL", detail))
        return (DONE if ok else FAILED), ("every check passed" if ok else "some checks failed")

    # -------------------------------------------------------------------- run
    STEPS = (
        ("public_url", "step_public_url"),
        ("secret", "step_secret"),
        ("app", "step_app"),
        ("bridge", "step_bridge"),
        ("install", "step_install"),
        ("webhook", "step_webhook"),
        ("doctor", "step_doctor"),
    )

    def run(self):
        reports = []
        stopped = None
        self.say("linear-agent setup")
        for name, method in self.STEPS:
            if stopped:
                reports.append({"name": name, "state": SKIPPED,
                                "detail": "not attempted: %s did not finish" % stopped})
                continue
            try:
                state, detail = getattr(self, method)()
            except Exception as exc:  # noqa: BLE001 - a step must never crash the run
                state, detail = FAILED, "%s: %s" % (type(exc).__name__, exc)
            reports.append({"name": name, "state": state, "detail": detail})
            if state == FAILED:
                stopped = name
        ok = all(r["state"] in (DONE, SKIPPED) for r in reports)
        result = {"ok": ok, "env_file": self.env_path, "steps": reports}
        self._remember(result)
        return result

    def _remember(self, result):
        """Leave a secret-free copy where the dashboard tab can read it.

        The webhook cannot be checked without a network call, so the tab shows what the
        last run found rather than guessing.
        """
        try:
            from .config import default_data_dir

            directory = default_data_dir()
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, "setup.json")
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(result, fh, indent=2)
                fh.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001 - remembering is a nicety, never a failure
            pass

    # ---------------------------------------------------------------- helpers
    def _urls(self):
        public = (self.env.get("LINEAR_AGENT_PUBLIC_URL") or "").rstrip("/")
        redirect = (self.env.get("LINEAR_REDIRECT_URI") or "") or (
            (public + "/oauth/callback") if public else "")
        return {
            "public_url": public,
            "webhook_url": (public + "/webhook") if public else "",
            "redirect_uri": redirect,
        }

    def _token_state(self):
        from .config import default_data_dir
        from .oauth import load_tokens

        path = (self.env.get("LINEAR_TOKEN_PATH")
                or os.path.join(default_data_dir(), "linear-tokens.json"))
        try:
            return path, load_tokens(path)
        except Exception:  # noqa: BLE001 - a corrupt file is a finding, not a crash
            return path, None

    def _graphql(self, token, query, variables):
        """One POST to Linear, keeping the status code: 403 has its own message here."""
        import urllib.error
        import urllib.request

        if self._transport:
            status, payload = self._transport(
                "https://api.linear.app/graphql",
                {"Content-Type": "application/json", "Authorization": token},
                json.dumps({"query": query, "variables": variables}).encode("utf-8"),
                30,
            )
            return status, payload
        request = urllib.request.Request(
            "https://api.linear.app/graphql",
            data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": token},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, {}
        except Exception:  # noqa: BLE001
            return 0, {}


def render(result, stream=None):
    """Human-readable checklist. Secrets never appear here."""
    stream = stream or sys.stdout
    marks = {DONE: "done", SKIPPED: "skipped", NEEDS_INPUT: "needs you", FAILED: "FAILED"}
    for step in result["steps"]:
        stream.write("  %-11s %-9s %s\n" % (step["name"], marks.get(step["state"], step["state"]),
                                            step["detail"]))
    stream.write("\n")
    if result["ok"]:
        stream.write("  installed. Linear issues that mention the app now reach Hermes.\n")
    else:
        pending = [s["name"] for s in result["steps"] if s["state"] in (NEEDS_INPUT, FAILED)]
        stream.write("  not finished: %s — re-run the same command when ready.\n"
                     % ", ".join(pending))
    return 0 if result["ok"] else 1
