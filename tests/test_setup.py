"""Tests for `hermes linear-agent setup`.

These pin the promises the command makes: a second run is a no-op, the generated secret
is stored at 0600 and never printed, a missing tunnel stops with instructions instead of
half-installing, a 403 explains the scope problem, and the JSON shape the dashboard tab
reads does not drift.
"""
from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest

from linear_hermes.setup import (
    DONE,
    FAILED,
    NEEDS_INPUT,
    SKIPPED,
    Setup,
)

TOKEN_KEY = "linear-tokens.json"


class FakeTransport:
    """Stands in for Linear's GraphQL endpoint and remembers what was created."""

    def __init__(self, status=200, nodes=None, create_status=200):
        self.status = status
        self.create_status = create_status
        self.nodes = list(nodes or [])
        self.created = []
        self.calls = []

    def __call__(self, url, headers, payload, timeout):
        body = json.loads(payload.decode("utf-8"))
        query = body.get("query", "")
        self.calls.append(query.strip().split("\n")[0])
        if "webhookCreate" in query:
            if self.create_status != 200:
                return self.create_status, {}
            data = body.get("variables", {}).get("input", {})
            node = {"id": "wh-%d" % (len(self.created) + 1),
                    "url": data.get("url"), "enabled": True}
            self.created.append(data)
            self.nodes.append(node)
            return 200, {"data": {"webhookCreate": {"success": True, "webhook": node}}}
        return self.status, {"data": {"webhooks": {"nodes": self.nodes}}}


class SetupBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_path = os.path.join(self.tmp.name, ".env")
        self.token_path = os.path.join(self.tmp.name, TOKEN_KEY)
        os.environ["LINEAR_TOKEN_PATH"] = self.token_path
        self.addCleanup(os.environ.pop, "LINEAR_TOKEN_PATH", None)
        # `setup` remembers its result for the dashboard; keep that out of ~/.hermes
        self._old_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = self.tmp.name
        self.addCleanup(self._restore_home)
        for key in ("LINEAR_AGENT_PUBLIC_URL", "LINEAR_CLIENT_ID", "LINEAR_CLIENT_SECRET",
                    "LINEAR_WEBHOOK_SECRET", "PORT"):
            os.environ.pop(key, None)
        self.output = io.StringIO()

    def _restore_home(self):
        if self._old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._old_home

    def make(self, **kwargs):
        kwargs.setdefault("env_path", self.env_path)
        kwargs.setdefault("interactive", False)
        kwargs.setdefault("yes", True)
        kwargs.setdefault("out", lambda line="": self.output.write(str(line) + "\n"))
        return Setup(**kwargs)

    def seed_env(self, **values):
        with open(self.env_path, "w") as fh:
            for key, value in values.items():
                fh.write("%s=%s\n" % (key, value))
        os.chmod(self.env_path, 0o600)

    def seed_token(self, access_token="tok-abc"):
        with open(self.token_path, "w") as fh:
            json.dump({"access_token": access_token, "refresh_token": "r"}, fh)
        os.chmod(self.token_path, 0o600)

    def read_env(self):
        with open(self.env_path) as fh:
            return dict(
                line.split("=", 1) for line in fh.read().splitlines()
                if "=" in line and not line.startswith("#")
            )


class WebhookTests(SetupBase):
    def test_a_second_run_does_not_create_a_second_webhook(self):
        self.seed_env(LINEAR_AGENT_PUBLIC_URL="https://example.test",
                      LINEAR_WEBHOOK_SECRET="s" * 64,
                      LINEAR_CLIENT_ID="cid", LINEAR_CLIENT_SECRET="csecret")
        self.seed_token()
        transport = FakeTransport()

        first = self.make(transport=transport)
        state, detail = first.step_webhook()
        self.assertEqual(state, DONE, detail)
        self.assertEqual(len(transport.created), 1)

        second = self.make(transport=transport)
        state, detail = second.step_webhook()
        self.assertEqual(state, SKIPPED, detail)
        self.assertEqual(len(transport.created), 1, "a re-run must not add a second webhook")
        self.assertIn("already points at", detail)

    def test_the_webhook_uses_the_stored_secret_and_the_public_url(self):
        self.seed_env(LINEAR_AGENT_PUBLIC_URL="https://example.test",
                      LINEAR_WEBHOOK_SECRET="a" * 64,
                      LINEAR_CLIENT_ID="cid", LINEAR_CLIENT_SECRET="csecret")
        self.seed_token()
        transport = FakeTransport()
        self.make(transport=transport).step_webhook()

        created = transport.created[0]
        self.assertEqual(created["url"], "https://example.test/webhook")
        self.assertEqual(created["secret"], "a" * 64)
        self.assertEqual(created["resourceTypes"], ["AgentSessionEvent"])
        self.assertTrue(created["allPublicTeams"])

    def test_a_403_explains_the_scope_problem_instead_of_a_traceback(self):
        self.seed_env(LINEAR_AGENT_PUBLIC_URL="https://example.test",
                      LINEAR_WEBHOOK_SECRET="s" * 64,
                      LINEAR_CLIENT_ID="cid", LINEAR_CLIENT_SECRET="csecret")
        self.seed_token()
        state, detail = self.make(transport=FakeTransport(create_status=403)).step_webhook()
        self.assertEqual(state, FAILED)
        self.assertIn("lacks write scope", detail)
        self.assertIn("Recreate the Linear app with read,write", detail)

    def test_a_401_listing_webhooks_also_reads_as_a_scope_problem(self):
        self.seed_env(LINEAR_AGENT_PUBLIC_URL="https://example.test",
                      LINEAR_WEBHOOK_SECRET="s" * 64,
                      LINEAR_CLIENT_ID="cid", LINEAR_CLIENT_SECRET="csecret")
        self.seed_token()
        state, detail = self.make(transport=FakeTransport(status=401)).step_webhook()
        self.assertEqual(state, FAILED)
        self.assertIn("write scope", detail)


class SecretTests(SetupBase):
    def test_the_generated_secret_is_stored_at_600_and_never_printed(self):
        setup = self.make()
        state, _detail = setup.step_secret()
        self.assertEqual(state, DONE)

        secret = self.read_env()["LINEAR_WEBHOOK_SECRET"]
        self.assertEqual(len(secret), 64, "32 random bytes as hex")
        mode = stat.S_IMODE(os.stat(self.env_path).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertNotIn(secret, self.output.getvalue(), "the secret must never be printed")

    def test_an_existing_secret_is_reused_not_replaced(self):
        self.seed_env(LINEAR_WEBHOOK_SECRET="b" * 64)
        state, _detail = self.make().step_secret()
        self.assertEqual(state, SKIPPED)
        self.assertEqual(self.read_env()["LINEAR_WEBHOOK_SECRET"], "b" * 64)


class TunnelTests(SetupBase):
    def test_a_missing_tunnel_stops_with_instructions(self):
        setup = self.make(which=lambda _name: None)
        state, detail = setup.step_public_url()
        self.assertEqual(state, FAILED)
        self.assertIn("cloudflared", detail)
        self.assertIn("brew install cloudflared", self.output.getvalue())

    def test_the_rest_of_the_run_is_skipped_once_the_tunnel_is_missing(self):
        result = self.make(which=lambda _name: None, doctor_fn=lambda: ([], False)).run()
        self.assertFalse(result["ok"])
        states = {step["name"]: step["state"] for step in result["steps"]}
        self.assertEqual(states["public_url"], FAILED)
        self.assertEqual(states["webhook"], SKIPPED, "nothing after a hard stop is attempted")
        self.assertIn("not attempted", result["steps"][-1]["detail"])

    def test_a_given_public_url_skips_the_tunnel_entirely(self):
        setup = self.make(which=lambda _name: self.fail("should not look for a tunnel"),
                          public_url="https://mine.example")
        state, detail = setup.step_public_url()
        self.assertEqual(state, DONE)
        self.assertIn("mine.example", detail)
        self.assertEqual(self.read_env()["LINEAR_AGENT_PUBLIC_URL"], "https://mine.example")


class ResumeTests(SetupBase):
    def test_a_configured_install_is_reported_as_already_done(self):
        self.seed_env(LINEAR_AGENT_PUBLIC_URL="https://example.test",
                      LINEAR_CLIENT_ID="cid", LINEAR_CLIENT_SECRET="csecret",
                      LINEAR_WEBHOOK_SECRET="s" * 64)
        self.seed_token()
        transport = FakeTransport(nodes=[{"id": "1", "url": "https://example.test/webhook",
                                          "enabled": True}])
        result = self.make(
            transport=transport,
            probe=lambda url, timeout: (200, b"ok"),
            doctor_fn=lambda: ([("config", True, "fine")], True),
        ).run()

        states = {step["name"]: step["state"] for step in result["steps"]}
        self.assertEqual(states["public_url"], DONE)
        self.assertEqual(states["secret"], SKIPPED)
        self.assertEqual(states["app"], DONE)
        self.assertEqual(states["bridge"], SKIPPED)
        self.assertEqual(states["install"], SKIPPED)
        self.assertEqual(states["webhook"], SKIPPED)
        self.assertTrue(result["ok"])
        self.assertEqual(transport.created, [], "a completed install creates nothing new")

    def test_without_values_and_without_a_terminal_it_asks_rather_than_guesses(self):
        setup = self.make(interactive=False, yes=False)
        state, detail = setup.step_app()
        self.assertEqual(state, NEEDS_INPUT)
        self.assertIn("linear.app/settings/api/applications/new", detail)


class JsonShapeTests(SetupBase):
    def test_the_checklist_shape_the_dashboard_reads_is_stable(self):
        self.seed_env(LINEAR_AGENT_PUBLIC_URL="https://example.test",
                      LINEAR_CLIENT_ID="cid", LINEAR_CLIENT_SECRET="csecret",
                      LINEAR_WEBHOOK_SECRET="s" * 64)
        self.seed_token()
        result = self.make(
            transport=FakeTransport(),
            probe=lambda url, timeout: (200, b"ok"),
            doctor_fn=lambda: ([("config", True, "fine")], True),
        ).run()

        self.assertEqual(sorted(result), ["env_file", "ok", "steps"])
        self.assertIsInstance(result["ok"], bool)
        self.assertEqual([s["name"] for s in result["steps"]],
                         ["public_url", "secret", "app", "bridge", "install", "webhook", "doctor"])
        allowed = {DONE, SKIPPED, NEEDS_INPUT, FAILED}
        for step in result["steps"]:
            self.assertEqual(sorted(step), ["detail", "name", "state"])
            self.assertIn(step["state"], allowed)
            self.assertIsInstance(step["detail"], str)
        self.assertTrue(result["ok"])
        json.dumps(result)   # must serialise: the tab reads it as JSON


class NoSecretLeakTests(SetupBase):
    def test_the_client_secret_never_appears_in_output(self):
        self.seed_env(LINEAR_AGENT_PUBLIC_URL="https://example.test")
        self.seed_token()
        setup = self.make(transport=FakeTransport(),
                          probe=lambda url, timeout: (200, b"ok"),
                          doctor_fn=lambda: ([], True))
        setup.env["LINEAR_CLIENT_SECRET"] = "super-secret-value"
        setup.run()
        self.assertNotIn("super-secret-value", self.output.getvalue())


class DashboardStateTests(unittest.TestCase):
    """The tab renders /state.setup, so its shape and its honesty are pinned here."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # The repo's own .env is configured on a real install; point the reader at an
        # empty one so this test measures the code, not this machine.
        self._old_env_file = os.environ.get("LINEAR_AGENT_ENV_FILE")
        os.environ["LINEAR_AGENT_ENV_FILE"] = os.path.join(self.tmp.name, ".env")
        self._old_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = self.tmp.name
        self.addCleanup(self._restore)
        for key in ("LINEAR_AGENT_PUBLIC_URL", "LINEAR_CLIENT_ID", "LINEAR_CLIENT_SECRET",
                    "LINEAR_WEBHOOK_SECRET", "LINEAR_TOKEN_PATH"):
            os.environ.pop(key, None)
        os.environ["LINEAR_TOKEN_PATH"] = os.path.join(self.tmp.name, "tokens.json")

        import linear_hermes.plugin_api as plugin_api

        self.plugin_api = plugin_api
        self._real = plugin_api.bridge_status
        plugin_api.bridge_status = lambda url=None, timeout=3.0: {"reachable": False,
                                                                 "error": "test"}
        self.addCleanup(setattr, plugin_api, "bridge_status", self._real)

    def _restore(self):
        if getattr(self, "_old_home", None) is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._old_home
        if self._old_env_file is None:
            os.environ.pop("LINEAR_AGENT_ENV_FILE", None)
        else:
            os.environ["LINEAR_AGENT_ENV_FILE"] = self._old_env_file

    def test_an_unconfigured_install_asks_for_everything(self):
        state = self.plugin_api.setup_state()
        names = [step["name"] for step in state["steps"]]
        self.assertEqual(names,
                         ["public_url", "secret", "app", "bridge", "install", "webhook"])
        self.assertFalse(state["ok"])
        self.assertEqual({s["state"] for s in state["steps"]}, {"needs_you", "unknown"})

    def test_a_configured_install_reports_done_steps(self):
        os.environ["LINEAR_AGENT_PUBLIC_URL"] = "https://example.test"
        os.environ["LINEAR_CLIENT_ID"] = "cid"
        os.environ["LINEAR_CLIENT_SECRET"] = "csecret"
        os.environ["LINEAR_WEBHOOK_SECRET"] = "s" * 64
        with open(os.environ["LINEAR_TOKEN_PATH"], "w") as fh:
            json.dump({"access_token": "tok"}, fh)
        state = self.plugin_api.setup_state()
        states = {step["name"]: step["state"] for step in state["steps"]}
        self.assertEqual(states["public_url"], "done")
        self.assertEqual(states["app"], "done")
        self.assertEqual(states["install"], "done")
        self.assertEqual(states["bridge"], "needs_you", "the probe said unreachable")

    def test_the_state_carries_no_secret_values(self):
        os.environ["LINEAR_WEBHOOK_SECRET"] = "supersecret" + "0" * 53
        os.environ["LINEAR_CLIENT_SECRET"] = "also-secret"
        blob = json.dumps(self.plugin_api.setup_state())
        self.assertNotIn("supersecret", blob)
        self.assertNotIn("also-secret", blob)


if __name__ == "__main__":
    unittest.main()
