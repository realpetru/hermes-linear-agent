"""Install-path correctness: config precedence, absolute paths, exchange, authorize."""
import os
import pathlib
import tempfile
import unittest

from linear_hermes.config import ConfigError, load_config

BASE = {"LINEAR_WEBHOOK_SECRET": "s3cret"}


def env(**overrides):
    values = dict(BASE)
    values.update(overrides)
    return values


class HomeTestCase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self._home = os.environ.get("HERMES_HOME")
        self._cwd = os.getcwd()
        # This suite asserts the *default* paths, so an ambient override from the
        # shell (or a previous suite) must not decide the answer.
        self._ambient = {k: os.environ.pop(k, None) for k in
                         ("LINEAR_AGENT_SETTINGS_DIR", "LINEAR_TOKEN_PATH", "STATE_DB")}
        os.environ["HERMES_HOME"] = self.home

    def tearDown(self):
        for key, value in getattr(self, "_ambient", {}).items():
            if value is not None:
                os.environ[key] = value
        if self._home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._home
        os.chdir(self._cwd)


class BindPortTests(HomeTestCase):
    def test_env_bind_and_port_are_honoured(self):
        cfg = load_config(env=env(BIND="0.0.0.0", PORT="8651"))
        self.assertEqual(cfg.bind, "0.0.0.0")
        self.assertEqual(cfg.port, 8651)

    def test_defaults_when_unset(self):
        cfg = load_config(env=env())
        self.assertEqual(cfg.bind, "127.0.0.1")
        self.assertEqual(cfg.port, 8650)

    def test_serve_defaults_do_not_override_env(self):
        """The CLI must not force BIND/PORT into os.environ (that beat .env)."""
        source = (pathlib.Path(__file__).resolve().parents[1] / "src/linear_hermes/plugin_api.py").read_text()
        self.assertIn('serve.add_argument("--bind", default=None', source)
        self.assertIn('serve.add_argument("--port", type=int, default=None', source)
        self.assertNotIn('os.environ.setdefault("BIND"', source, "a forced default beats .env")
        # the only writes must sit inside "the user actually passed the flag" guards
        self.assertIn('if getattr(args, "bind", None):', source)
        self.assertIn('if getattr(args, "port", None):', source)


class DataPathTests(HomeTestCase):
    def test_token_path_default_is_absolute(self):
        cfg = load_config(env=env())
        self.assertTrue(os.path.isabs(cfg.linear_token_path))
        self.assertTrue(cfg.linear_token_path.endswith("linear-tokens.json"))
        self.assertIn("plugin-data", cfg.linear_token_path)

    def test_state_db_lives_beside_the_token(self):
        cfg = load_config(env=env())
        self.assertEqual(os.path.dirname(cfg.db_path), os.path.dirname(cfg.linear_token_path))
        self.assertNotIn("hermes-linear-agent", cfg.db_path, "the old /tmp default is gone")

    def test_relative_token_path_is_made_absolute(self):
        cfg = load_config(env=env(LINEAR_TOKEN_PATH="data/linear-tokens.json"))
        self.assertTrue(os.path.isabs(cfg.linear_token_path))
        self.assertTrue(cfg.linear_token_path.endswith(os.path.join("data", "linear-tokens.json")))

    def test_moving_cwd_keeps_the_token_visible(self):
        first = load_config(env=env()).linear_token_path
        os.chdir(tempfile.mkdtemp())
        second = load_config(env=env()).linear_token_path
        self.assertEqual(first, second)


class UrlTests(HomeTestCase):
    def test_redirect_derives_from_public_url(self):
        cfg = load_config(env=env(LINEAR_AGENT_PUBLIC_URL="https://host.example/"))
        self.assertEqual(cfg.public_url, "https://host.example")
        self.assertEqual(cfg.redirect_uri, "https://host.example/oauth/callback")

    def test_explicit_redirect_wins(self):
        cfg = load_config(env=env(
            LINEAR_AGENT_PUBLIC_URL="https://host.example",
            LINEAR_REDIRECT_URI="http://localhost:8650/oauth/callback",
        ))
        self.assertEqual(cfg.redirect_uri, "http://localhost:8650/oauth/callback")

    def test_no_fallback_to_the_internal_bridge_url(self):
        cfg = load_config(env=env())
        self.assertEqual(cfg.redirect_uri, "", "an internal address is not a Linear callback")


class ExchangeEnvTests(HomeTestCase):
    def test_exchange_reads_the_credentials_file(self):
        """The documented fallback must load the credentials file, not only os.environ.

        `credentials_file()` is $LINEAR_AGENT_ENV_FILE when set, else the Hermes profile
        .env, else ./.env in a standalone checkout — one store for setup, doctor and this.
        """
        work = tempfile.mkdtemp()
        credentials = pathlib.Path(work, ".env")
        os.environ["LINEAR_AGENT_ENV_FILE"] = str(credentials)
        self.addCleanup(lambda: os.environ.pop("LINEAR_AGENT_ENV_FILE", None))
        credentials.write_text(
            "LINEAR_CLIENT_ID=cid-from-dotenv\n"
            "LINEAR_CLIENT_SECRET=csec\n"
            "LINEAR_AGENT_PUBLIC_URL=https://host.example\n"
        )
        os.chdir(work)
        for key in ("LINEAR_CLIENT_ID", "LINEAR_CLIENT_SECRET", "LINEAR_AGENT_PUBLIC_URL", "LINEAR_REDIRECT_URI"):
            os.environ.pop(key, None)

        import linear_hermes.exchange as exchange

        calls = {}

        def fake_exchange(client_id, client_secret, code, redirect_uri, transport):
            calls.update(client_id=client_id, client_secret=client_secret, code=code, redirect=redirect_uri)
            return {"access_token": "tok", "refresh_token": "ref", "expires_at": "2030-01-01T00:00:00+00:00"}

        original = exchange.exchange_code
        exchange.exchange_code = fake_exchange
        try:
            code = exchange.main(["https://host.example/oauth/callback?code=abc123"])
        finally:
            exchange.exchange_code = original

        self.assertEqual(code, 0)
        self.assertEqual(calls["client_id"], "cid-from-dotenv")
        self.assertEqual(calls["code"], "abc123")
        self.assertEqual(calls["redirect"], "https://host.example/oauth/callback")


class AuthorizeGuardTests(HomeTestCase):
    def test_authorize_refuses_without_any_install_key(self):
        """No LINEAR_STATE_KEY and no webhook secret: printing a link would mint a dead one."""
        from linear_hermes import plugin_api

        original = plugin_api.read_setting
        plugin_api.read_setting = lambda name, default=None, env=None: (
            "cid" if name == "LINEAR_CLIENT_ID" else (default if default is not None else "")
        )
        try:
            doc = plugin_api.tool_authorize_url()
        finally:
            plugin_api.read_setting = original
        import json

        payload = json.loads(doc)
        self.assertFalse(payload["ok"])
        self.assertIn("LINEAR_STATE_KEY", payload["next_step"])

    def test_authorize_signs_with_the_state_key_setup_generates(self):
        """The whole P0: setup writes LINEAR_STATE_KEY, the link must be signed with it."""
        from linear_hermes import plugin_api, oauth

        values = {"LINEAR_CLIENT_ID": "cid", "LINEAR_STATE_KEY": "k" * 64,
                  "LINEAR_WEBHOOK_SECRET": "linear-signing",
                  "LINEAR_AGENT_PUBLIC_URL": "https://example.test"}
        original = plugin_api.read_setting
        plugin_api.read_setting = lambda name, default=None, env=None: values.get(name, default or "")
        try:
            payload = __import__("json").loads(plugin_api.tool_authorize_url())
        finally:
            plugin_api.read_setting = original
        self.assertTrue(payload["ok"], payload)
        state = payload["authorize_url"].split("state=")[1].split("&")[0]
        self.assertTrue(oauth.verify_state("k" * 64, state), "the server verifies cfg.state_key")
        self.assertFalse(oauth.verify_state("linear-signing", state),
                         "signing with the webhook secret is what produced dead links")


if __name__ == "__main__":
    unittest.main()
