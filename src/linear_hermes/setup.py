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
* the **public URL** is whatever the operator already runs — a reverse proxy, a named
  tunnel, a small VPS — and setup proves it answers before going on.

Two pastes remain, both unavoidable: the Linear app's client id and client secret.

Nothing here prints a secret. Values are written to ``.env`` at mode 0600.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
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

# An agent's webhook is configured on the Linear app itself: `webhookCreate` rejects
# AgentSessionEvent for every participant token (verified against the live API). Setup
# therefore guides the pasting instead of mutating, and only lists webhooks to warn about
# a duplicate URL — never to create anything.

PUBLIC_URL_HELP = """\
Linear needs a public HTTPS address it can reach: one for the OAuth callback, one for the
webhook. This plugin does not create one — it uses the one you already have.

Point any tunnel or reverse proxy at the bridge port (8650 by default) and pass the
address in:

  hermes linear-agent setup --public-url https://linear.example.com

Or set LINEAR_AGENT_PUBLIC_URL in .env. A reverse proxy, a managed tunnel, or a small
VPS all work; whatever you already run is fine.
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
                 which=None, runner=None, probe=None, sleep=None, out=None,
                 transport=None, timeout_install=300, timeout_bridge=60,
                 clock=None, doctor_fn=None):
        from .config import default_env_file, load_dotenv

        self.env_path = env_path or default_env_file()
        self.public_url_flag = (public_url or "").rstrip("/")
        self.yes = yes
        self.interactive = (sys.stdin.isatty() if interactive is None else interactive)
        self._which = which or shutil.which
        self._runner = runner or subprocess.Popen
        self._probe = probe
        self.sleep = sleep or time.sleep
        self.out = out or print
        self._transport = transport
        self._clock = clock or time.time
        self.timeout_install = timeout_install
        self.timeout_bridge = timeout_bridge
        self._doctor = doctor_fn
        self.env = dict(load_dotenv(self.env_path))
        self.env.update({k: v for k, v in os.environ.items() if k.startswith("LINEAR")})
        self.bridge_process = None
        self.bridge_log = None
        self._bridge_handle = None

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
        # mkstemp creates the file 0600 before any byte of a secret lands in it: the old
        # write-then-chmod order had a window, and a crash inside it left 0644 behind.
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".env.", text=True)
        try:
            with os.fdopen(fd, "w") as fh:
                os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
                fh.write("\n".join(merged).rstrip() + "\n")
            os.replace(tmp, self.env_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
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
        if not existing:
            self.say(PUBLIC_URL_HELP)
            return NEEDS_INPUT, ("set LINEAR_AGENT_PUBLIC_URL or pass --public-url, then re-run "
                                 "setup — every later step is skipped until it is set")

        self._write_env({"LINEAR_AGENT_PUBLIC_URL": existing})
        # Collect only. Probing here was the fresh-host dead end: on a machine where the
        # bridge is not up yet, /healthz cannot answer, step 1 failed, and every later
        # step — including starting the bridge — was skipped. Reachability is proved by
        # step_public_check, once there is something to reach.
        return DONE, "%s stored; reachability is checked once the bridge is up" % existing

    def step_public_check(self):
        """Prove Linear can reach us: the bridge is running, so go through the public URL."""
        public = (self.env.get("LINEAR_AGENT_PUBLIC_URL") or "").rstrip("/")
        if not public:
            return NEEDS_INPUT, ("no public URL yet — set LINEAR_AGENT_PUBLIC_URL or pass "
                                 "--public-url, then re-run setup")
        health = public + "/healthz"
        probe = self._probe or self._default_probe
        try:
            status, _body = probe(health, 5)
        except Exception as exc:  # noqa: BLE001 - unreachable is an answer, not a crash
            return FAILED, "%s did not answer (%s) — is the proxy pointing at the bridge?" % (health, exc)
        if status == 200:
            return DONE, "%s answers /healthz" % public
        if status == 503 and "not_configured" in (
                _body.decode("utf-8", "replace") if isinstance(_body, bytes) else str(_body)):
            return FAILED, ("%s/healthz answered 503 not_configured — the bridge is up but has no "
                            "LINEAR_WEBHOOK_SECRET yet: finish the webhook step, restart the "
                            "bridge, then re-run" % public)
        if status in (401, 403):
            return FAILED, ("%s answered %s — the proxy is blocking it; Linear must be able to "
                            "reach /healthz" % (health, status))
        return FAILED, ("%s answered %s — the bridge is not reachable at that address"
                        % (health, status or "nothing"))

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

    def step_state_key(self):
        """A key for signing the OAuth install link. Not Linear's signing secret.

        They are different things and are stored in different variables: this one only
        proves the callback came back to a link we minted.
        """
        current = (self.env.get("LINEAR_STATE_KEY") or "").strip()
        if current:
            return SKIPPED, "install-link key already set"
        self._write_env({"LINEAR_STATE_KEY": os.urandom(32).hex()})
        mode = _mode_of(self.env_path)
        return DONE, ("generated a key for signing the install link only "
                      "(%s, mode 0%o) — this is not Linear's signing secret"
                      % (self.env_path, mode or 0))

    def step_bridge(self):
        port = self.env.get("PORT") or "8650"
        # Use the bridge that is configured, not a hardcoded localhost: a containerised
        # install reaches the bridge on the host (e.g. 172.17.0.1:8650), and a bridge on
        # another machine is legitimate too. Probing 127.0.0.1 there always fails.
        base = (self.env.get("LINEAR_AGENT_BRIDGE_URL") or "").rstrip("/") or ("http://127.0.0.1:%s" % port)
        health = base + "/healthz"
        state = self._health(health)
        if state == "ok":
            return SKIPPED, "already answering at %s" % base
        if state == "not_configured":
            return SKIPPED, ("already answering at %s (not configured yet — the webhook step "
                             "supplies the secret)" % base)

        local = any(base.startswith(p) for p in ("http://127.0.0.1", "http://localhost", "http://[::1]"))
        if not local:
            return NEEDS_INPUT, (
                "the configured bridge (%s) is not answering and it is not on this machine, "
                "so setup will not try to start it — start it there, then re-run" % base)

        compose = os.path.exists("docker-compose.yml")
        docker = self._which("docker")
        try:
            if compose and docker:
                self._runner([docker, "compose", "up", "-d"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
                started = "docker compose up -d"
            else:
                # Spawn with THIS plugin's src on the path. `python -m linear_hermes` resolves
                # the package against the caller's CWD, so from anywhere but the repo root the
                # child died with ModuleNotFoundError — invisibly, because stderr was DEVNULL.
                env = self._child_env()
                env["PYTHONPATH"] = self._plugin_src(env)
                self.bridge_log = os.path.join(tempfile.gettempdir(), "linear-agent-bridge.log")
                self._bridge_handle = open(self.bridge_log, "wb")
                self.bridge_process = self._runner(
                    [sys.executable, "-m", "linear_hermes"],
                    stdout=self._bridge_handle, stderr=subprocess.STDOUT,
                    env=env,
                )
                started = "python -m linear_hermes (pid %s, log %s)" % (
                    getattr(self.bridge_process, "pid", "?"), self.bridge_log)
        except OSError as exc:
            return FAILED, "could not start the bridge: %s" % exc

        deadline = self._clock() + self.timeout_bridge
        while self._clock() < deadline:
            state = self._health(health)
            if state == "ok":
                self._close_bridge_log()
                return DONE, "%s, answering at %s" % (started, health)
            if state == "not_configured":
                # Up and honest about what it still needs: keep going, the webhook step is next.
                self._close_bridge_log()
                return DONE, ("%s, up at %s — not configured yet (no LINEAR_WEBHOOK_SECRET), "
                              "which the webhook step supplies" % (started, health))
            code = getattr(self.bridge_process, "poll", lambda: None)()
            if code is not None:
                # Fail on evidence instead of waiting out the timeout: a child that exits
                # immediately will never answer, and 60 s of silence taught nobody anything.
                tail = self._bridge_tail()
                self._close_bridge_log()
                return FAILED, ("the bridge exited with code %s before answering%s — log: %s"
                                % (code, (" — " + tail) if tail else "", self.bridge_log))
            self.sleep(1)
        self._close_bridge_log()
        return FAILED, ("started %s but %s never answered — check the logs with "
                        "`docker compose logs`, %s or `hermes linear-agent status`"
                        % (started, health, self.bridge_log or "the service log"))

    def _plugin_src(self, env):
        """This plugin's own ``src`` directory, ahead of whatever PYTHONPATH was inherited."""
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        parts = [here] + [p for p in (env.get("PYTHONPATH") or "").split(os.pathsep) if p]
        return os.pathsep.join(parts)

    def _bridge_tail(self, lines=4):
        """The end of the child's log, so a failure names its own cause."""
        if not self.bridge_log:
            return ""
        try:
            with open(self.bridge_log, "rb") as fh:
                text = fh.read().decode("utf-8", "replace").strip().splitlines()
        except OSError:
            return ""
        return " / ".join(line.strip() for line in text[-lines:] if line.strip())

    def _close_bridge_log(self):
        if self._bridge_handle is not None:
            try:
                self._bridge_handle.close()
            except Exception:  # noqa: BLE001 - a closed log is not a setup failure
                pass
            self._bridge_handle = None

    def _child_env(self):
        env = dict(os.environ)
        env.update({k: v for k, v in self.env.items() if k.startswith("LINEAR")})
        return env

    def _health(self, url):
        """One of "ok", "not_configured" or "down".

        A bridge that answers 503 `not_configured` is UP — it just has no webhook secret yet,
        and that is exactly the state a fresh install is in at this point. Treating it as
        "down" made setup fail the bridge step on its own successful start.
        """
        probe = self._probe or self._default_probe
        try:
            status, body = probe(url, 3)
        except Exception:  # noqa: BLE001 - connection refused is the normal first answer
            return "down"
        if status == 200:
            return "ok"
        if status == 503:
            text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
            if "not_configured" in text:
                return "not_configured"
        return "down"

    @staticmethod
    def _default_probe(url, timeout):
        import urllib.error
        import urllib.request

        # Identify ourselves: a CDN bot rule answers the default ``Python-urllib``
        # agent with 403, which would fail this step on a perfectly good public URL.
        # Same reason ``doctor`` sends one (1.3.3).
        try:
            from .plugin_api import user_agent as _user_agent
        except Exception:  # noqa: BLE001 - standalone runs without the package
            _user_agent = lambda: "linear-agent-setup"
        request = urllib.request.Request(url, headers={"User-Agent": _user_agent()})

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            # Keep the body: a 503 from the bridge carries {"status": "not_configured", ...},
            # and that JSON is the difference between "down" and "up, missing a secret".
            return exc.code, exc.read()
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
        state = make_state((self.env.get("LINEAR_STATE_KEY") or "").strip())
        url = authorize_url(client_id, self._urls()["redirect_uri"], state=state)
        self.say("")
        self.say("  Install the app in your workspace (as an admin):")
        self.say("    %s" % url)
        self.say("")

        # Nobody can click in a scripted run, so polling for the callback only blocks:
        # return the URL at once and let the next invocation pick the token up.
        if self.yes or not self.interactive:
            return NEEDS_INPUT, ("open this URL as a workspace admin, then re-run setup to "
                                 "finish: %s" % url)

        self.say("  Waiting up to %d s for the callback…" % self.timeout_install)
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
        """Point the human at the app's Webhook URL field and store what Linear gives back.

        There is no API for this: an agent's webhook lives in the app's own configuration,
        and `webhookCreate` refuses AgentSessionEvent. Four things a person must paste are
        the honest floor, and this is the third.
        """
        urls = self._urls()
        target = urls["webhook_url"]
        if not target:
            return NEEDS_INPUT, "no public URL — Linear has nowhere to send events"

        existing = (self.env.get("LINEAR_WEBHOOK_SECRET") or "").strip()
        if existing:
            return SKIPPED, "Linear's signing secret is already stored in %s" % self.env_path

        self.say("")
        self.say("  In the Linear app, on the same page as the callback URL:")
        self.say("    Webhook URL     %s" % target)
        self.say("    Category        Agent session events")
        self.say("    Signing secret  Linear shows it once — copy it back here")
        self.say("    %s" % APP_URL)
        self.say("")
        self._warn_duplicate_webhook(target)

        if self.yes or not self.interactive:
            return NEEDS_INPUT, (
                "paste %s into the app's Webhook URL at %s, pick Agent session events, "
                "then re-run setup with the signing secret Linear shows" % (target, APP_URL))

        secret = self._ask("Webhook signing secret", secret=True)
        if not secret:
            return NEEDS_INPUT, ("no signing secret given — copy it from %s" % APP_URL)
        self._write_env({"LINEAR_WEBHOOK_SECRET": secret})
        mode = _mode_of(self.env_path)
        return DONE, "signing secret stored (%s, mode 0%o)" % (self.env_path, mode or 0)

    def _warn_duplicate_webhook(self, target):
        """If another webhook already posts to this URL, Linear will deliver twice."""
        _path, record = self._token_state()
        if not record:
            return
        status, payload = self._graphql(str(record.get("access_token") or ""), WEBHOOKS_QUERY, {})
        if status != 200:
            return
        nodes = ((payload.get("data") or {}).get("webhooks") or {}).get("nodes") or []
        for node in nodes:
            if str(node.get("url") or "").rstrip("/") == target:
                self.say("  note: another webhook already points at %s — with the app's own"
                         % target)
                self.say("        webhook you would get every event delivered twice.")
                return

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
        ("public_url", "step_public_url"),      # collect the address
        ("state_key", "step_state_key"),
        # The bridge needs no Linear credentials: start it before the step that waits for a
        # human, so a first run leaves something running instead of skipping everything.
        ("bridge", "step_bridge"),
        ("app", "step_app"),
        ("public_check", "step_public_check"),  # prove Linear can reach what we just started
        ("install", "step_install"),
        ("webhook", "step_webhook"),
        ("doctor", "step_doctor"),
    )

    def run(self):
        reports = []
        stopped = None
        reason = ""

        self.say("linear-agent setup")
        for name, method in self.STEPS:
            if stopped:
                reports.append({"name": name, "state": SKIPPED,
                                "detail": "not attempted: %s %s" % (
                                    stopped,
                                    "needs input" if reason == NEEDS_INPUT else "did not finish")})
                continue
            try:
                state, detail = getattr(self, method)()
            except Exception as exc:  # noqa: BLE001 - a step must never crash the run
                state, detail = FAILED, "%s: %s" % (type(exc).__name__, exc)
            reports.append({"name": name, "state": state, "detail": detail})
            # `needs_input` is a stop, not a warning: continuing past it used to skip the
            # bridge start and then report every later step as "not attempted".
            if state in (FAILED, NEEDS_INPUT):
                stopped, reason = name, state
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
