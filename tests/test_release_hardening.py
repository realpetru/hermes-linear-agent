"""Release hardening: the security P1s, each pinned by an assertion.

Every test here corresponds to a finding in the pre-publication audit: secrets in the log,
markup from a query string, a world-readable window on credential files, a child process that
inherited every bridge secret, a personal API key that posted as a human, and a container that
crash-looped instead of explaining itself.
"""
from __future__ import annotations

import io
import json
import logging
import os
import pathlib
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from linear_hermes.config import Config, load_config
from linear_hermes.oauth import make_state, save_tokens
from linear_hermes.server import serve, serve_unconfigured

import isolation
import local_endpoints


def make_cfg(port, **over):
    base = dict(
        webhook_secret="topsecret", linear_api_key="", hermes_adapter="subprocess",
        gateway_url="", gateway_token="", hermes_bin="hermes", port=port,
        bind="127.0.0.1", max_concurrency=1, db_path="/tmp/x.sqlite3",
        agent_name="Hermes", log_level="INFO", state_key="state-key-for-tests",
    )
    base.update(over)
    return Config(**base)


def get(port, path):
    try:
        with urllib.request.urlopen(local_endpoints.url(port, path), timeout=5) as response:
            return response.status, response.read().decode("utf-8", "replace"), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace"), dict(exc.headers)


def post(port, path, body=b"{}"):
    request = urllib.request.Request(local_endpoints.url(port, path), data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


class ServerCase(unittest.TestCase):
    cfg_kwargs: dict = {}

    def setUp(self):
        self.httpd = serve(make_cfg(0, **self.cfg_kwargs), lambda payload, headers: None)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)


class LoggingTests(ServerCase):
    def test_the_authorization_code_and_state_never_reach_the_log(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("linear_hermes.server")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        self.addCleanup(logger.removeHandler, handler)
        state = make_state("state-key-for-tests")
        get(self.port, "/oauth/callback?code=CODE-SECRET-123&state=" + state)
        logged = stream.getvalue()
        self.assertIn("/oauth/callback", logged, "the path is still useful")
        self.assertNotIn("CODE-SECRET-123", logged, "an authorization code is a credential")
        self.assertNotIn(state.split(".")[1], logged, "the signature is a credential")


class CallbackMarkupTests(ServerCase):
    def test_an_error_string_cannot_inject_markup(self):
        state = make_state("state-key-for-tests")
        status, body, headers = get(self.port, "/oauth/callback?state=" + state +
                                    "&error=%3Cscript%3Ealert(1)%3C/script%3E")
        self.assertEqual(status, 400)
        self.assertNotIn("<script>", body, "the callback page must not render caller markup")
        self.assertIn("&lt;script&gt;", body)
        self.assertIn("Content-Security-Policy", {k.title(): v for k, v in headers.items()})

    def test_the_install_page_carries_a_content_security_policy(self):
        state = make_state("state-key-for-tests")
        _status, _body, headers = get(self.port, "/oauth/callback?state=" + state)
        lowered = {k.lower(): v for k, v in headers.items()}
        self.assertIn("content-security-policy", lowered)
        self.assertIn("default-src 'none'", lowered["content-security-policy"])


class CredentialModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_the_token_file_is_0600_from_the_first_byte(self):
        path = os.path.join(self.tmp.name, "tokens.json")
        save_tokens(path, {"access_token": "tok"})
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual([n for n in os.listdir(self.tmp.name) if n.endswith(".tmp")], [],
                         "no temporary file is left behind")

    def test_a_failed_write_leaves_no_temporary_file(self):
        path = os.path.join(self.tmp.name, "tokens.json")
        with mock.patch("json.dump", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                save_tokens(path, {"access_token": "tok"})
        self.assertEqual(os.listdir(self.tmp.name), [], "the temp file must be removed")

    def test_the_env_file_is_0600_and_written_atomically(self):
        from linear_hermes.setup import Setup

        # Setup mirrors what it writes into os.environ; keep that out of the other tests.
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        env_path = os.path.join(self.tmp.name, ".env")
        setup = Setup(env_path=env_path, interactive=False, yes=True, out=lambda line="": None)
        setup._write_env({"LINEAR_STATE_KEY": "k" * 64})
        self.assertEqual(stat.S_IMODE(os.stat(env_path).st_mode), 0o600)
        self.assertEqual([n for n in os.listdir(self.tmp.name) if n.startswith(".env.")], [])


class SubprocessEnvironmentTests(unittest.TestCase):
    def test_the_child_does_not_inherit_the_bridges_secrets(self):
        from linear_hermes.adapters import SubprocessAdapter

        secret_env = {
            "PATH": "/usr/bin", "HOME": "/Users/someone",
            "LINEAR_CLIENT_SECRET": "client-secret", "LINEAR_WEBHOOK_SECRET": "signing-secret",
            "HERMES_GATEWAY_TOKEN": "gateway-token", "LINEAR_API_KEY": "personal-key",
        }
        captured = {}

        class Done:
            returncode = 0
            stdout = "ok"
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            captured["cmd"] = cmd
            return Done()

        with mock.patch.dict(os.environ, secret_env, clear=True):
            with mock.patch("subprocess.run", side_effect=fake_run):
                SubprocessAdapter(hermes_bin="hermes").run("prompt")
        env = captured["env"]
        self.assertEqual(sorted(env), ["HOME", "PATH"], "only what the CLI needs to start")
        for leaked in ("client-secret", "signing-secret", "gateway-token", "personal-key"):
            self.assertNotIn(leaked, json.dumps(env))


class PersonalKeyTests(unittest.TestCase):
    def test_a_personal_key_alone_does_not_switch_identity(self):
        cfg = load_config(env={
            "LINEAR_WEBHOOK_SECRET": "s", "LINEAR_API_KEY": "personal",
            "HERMES_ADAPTER": "subprocess", "HERMES_GATEWAY_URL": "",
        })
        self.assertEqual(cfg.linear_api_key, "personal")
        self.assertFalse(cfg.allow_personal_key, "the human-identity fallback is opt-in")

    def test_it_can_be_turned_on_explicitly(self):
        cfg = load_config(env={
            "LINEAR_WEBHOOK_SECRET": "s", "LINEAR_API_KEY": "personal",
            "LINEAR_ALLOW_PERSONAL_KEY": "1", "HERMES_ADAPTER": "subprocess",
        })
        self.assertTrue(cfg.allow_personal_key)


class UnconfiguredBootTests(unittest.TestCase):
    """A container without its secret must be inspectable, not a crash loop."""

    def setUp(self):
        self.httpd = serve_unconfigured("127.0.0.1", 0, "LINEAR_WEBHOOK_SECRET is not set",
                                        ["LINEAR_WEBHOOK_SECRET"])
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def test_health_says_not_configured_and_why(self):
        status, body, _headers = get(self.port, "/healthz")
        self.assertEqual(status, 503)
        payload = json.loads(body)
        self.assertEqual(payload["status"], "not_configured")
        self.assertIn("LINEAR_WEBHOOK_SECRET", payload["missing"])

    def test_status_is_readable(self):
        status, body, _headers = get(self.port, "/status")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "not_configured")

    def test_a_webhook_is_refused_not_ignored(self):
        status, body = post(self.port, "/webhook")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"], "not_configured")


class HermeticTests(unittest.TestCase):
    """The suite must not depend on what happens to be in the working directory."""

    def setUp(self):
        isolation.apply(self)

    def test_a_dotenv_in_the_checkout_is_ignored(self):
        from linear_hermes import plugin_api

        work = tempfile.mkdtemp()
        saved = os.getcwd()
        try:
            with open(os.path.join(work, ".env"), "w") as fh:
                fh.write("LINEAR_CLIENT_ID=from-the-checkout\n")
            os.chdir(work)
            view = plugin_api._env_view()
            self.assertNotEqual(view.get("LINEAR_CLIENT_ID"), "from-the-checkout",
                                "a stray .env must not configure a test")
        finally:
            os.chdir(saved)

    def test_the_isolated_file_is_the_one_every_command_uses(self):
        from linear_hermes.config import credentials_file

        self.assertEqual(credentials_file(), os.path.abspath(isolation.path()))
