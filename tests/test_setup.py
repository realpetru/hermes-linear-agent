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
        # Snapshot and clear every LINEAR_* key first, so a value written by one test
        # cannot change the answer another test gets — and so clearing cannot undo the
        # token path set below.
        self._saved_env = {k: v for k, v in os.environ.items() if k.startswith("LINEAR")}
        self.addCleanup(self._restore_linear_env)
        for key in list(self._saved_env):
            os.environ.pop(key, None)

        self.token_path = os.path.join(self.tmp.name, TOKEN_KEY)
        os.environ["LINEAR_TOKEN_PATH"] = self.token_path
        self.addCleanup(lambda: os.environ.pop("LINEAR_TOKEN_PATH", None))
        # `setup` remembers its result for the dashboard; keep that out of ~/.hermes
        self._old_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = self.tmp.name
        self.addCleanup(self._restore_home)
        self.output = io.StringIO()

    def _restore_linear_env(self):
        for key in [k for k in os.environ if k.startswith("LINEAR")]:
            os.environ.pop(key, None)
        os.environ.update(self._saved_env)

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


class WebhookStepTests(SetupBase):
    """Linear's own API refuses to create an agent-session webhook: this step is a guide."""

    def seed_base(self, **extra):
        values = dict(LINEAR_AGENT_PUBLIC_URL="https://example.test",
                      LINEAR_CLIENT_ID="cid", LINEAR_CLIENT_SECRET="csecret",
                      LINEAR_STATE_KEY="k" * 64)
        values.update(extra)
        self.seed_env(**values)
        self.seed_token()

    def test_it_never_creates_a_webhook(self):
        self.seed_base()
        transport = FakeTransport()
        self.make(transport=transport).step_webhook()
        self.assertFalse(
            any("webhookCreate" in call for call in transport.calls),
            "webhookCreate rejects AgentSessionEvent; setup must never call it",
        )

    def test_it_prints_the_url_and_the_category_to_paste(self):
        self.seed_base()
        setup = self.make(transport=FakeTransport())
        state, detail = setup.step_webhook()
        printed = self.output.getvalue()
        self.assertIn("https://example.test/webhook", printed)
        self.assertIn("Agent session events", printed)
        self.assertEqual(state, NEEDS_INPUT)
        self.assertIn("https://linear.app/settings/api/applications/new", detail)

    def test_a_present_signing_secret_is_stored_at_600_and_never_echoed(self):
        self.seed_base()
        setup = self.make(transport=FakeTransport(), interactive=True, yes=False)
        setup._ask = lambda label, secret=False: "linear-signing-secret"
        state, _detail = setup.step_webhook()

        self.assertEqual(state, DONE)
        stored = self.read_env()["LINEAR_WEBHOOK_SECRET"]
        self.assertEqual(stored, "linear-signing-secret")
        self.assertEqual(stat.S_IMODE(os.stat(self.env_path).st_mode), 0o600)
        self.assertNotIn("linear-signing-secret", self.output.getvalue())
        self.assertNotIn("linear-signing-secret", json.dumps(setup.run()))

    def test_an_empty_answer_asks_again_rather_than_storing_nothing(self):
        self.seed_base()
        setup = self.make(transport=FakeTransport(), interactive=True, yes=False)
        setup._ask = lambda label, secret=False: ""
        state, detail = setup.step_webhook()
        self.assertEqual(state, NEEDS_INPUT)
        self.assertIn("no signing secret given", detail)
        self.assertNotIn("LINEAR_WEBHOOK_SECRET", self.read_env())

    def test_an_existing_signing_secret_is_skipped(self):
        self.seed_base(LINEAR_WEBHOOK_SECRET="already-there")
        state, detail = self.make(transport=FakeTransport()).step_webhook()
        self.assertEqual(state, SKIPPED)
        self.assertIn("already stored", detail)

    def test_a_duplicate_webhook_url_is_warned_about(self):
        self.seed_base()
        transport = FakeTransport(nodes=[{"id": "1", "url": "https://example.test/webhook",
                                          "enabled": True}])
        self.make(transport=transport).step_webhook()
        self.assertIn("twice", self.output.getvalue())

    def test_a_403_on_the_list_query_only_warns_and_does_not_fail_the_step(self):
        self.seed_base()
        state, _detail = self.make(transport=FakeTransport(status=403)).step_webhook()
        self.assertEqual(state, NEEDS_INPUT, "the read is advisory; the guide still works")


class StateKeyTests(SetupBase):
    def test_the_generated_key_signs_the_install_link_not_webhooks(self):
        setup = self.make()
        state, detail = setup.step_state_key()
        self.assertEqual(state, DONE)

        secret = self.read_env()["LINEAR_STATE_KEY"]
        self.assertEqual(len(secret), 64, "32 random bytes as hex")
        mode = stat.S_IMODE(os.stat(self.env_path).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertNotIn(secret, self.output.getvalue(), "the secret must never be printed")
        self.assertIn("mode 0600", detail, "the mode is reported in octal, not decimal")
        self.assertIn("not Linear's signing secret", detail)

    def test_an_existing_key_is_reused_not_replaced(self):
        self.seed_env(LINEAR_STATE_KEY="b" * 64)
        state, _detail = self.make().step_state_key()
        self.assertEqual(state, SKIPPED)
        self.assertEqual(self.read_env()["LINEAR_STATE_KEY"], "b" * 64)


class PublicUrlTests(SetupBase):
    def test_without_a_url_setup_says_what_is_needed_and_stops(self):
        setup = self.make(probe=lambda url, timeout: (200, b"ok"))
        state, detail = setup.step_public_url()
        self.assertEqual(state, NEEDS_INPUT)
        self.assertIn("--public-url", detail)
        printed = self.output.getvalue()
        self.assertIn("--public-url", printed)
        # "brew install ..." was the old tunnel advice; setup must not tell anyone to
        # install a networking binary any more (the repo-wide scan enforces the rest).
        self.assertNotIn("brew install", printed)

    def test_the_url_is_proven_with_a_probe_before_the_run_goes_on(self):
        setup = self.make(public_url="https://mine.example",
                          probe=lambda url, timeout: (200, b"ok"))
        state, detail = setup.step_public_url()
        self.assertEqual(state, DONE)
        self.assertIn("/healthz", detail)
        self.assertEqual(self.read_env()["LINEAR_AGENT_PUBLIC_URL"], "https://mine.example")

    def test_a_url_that_does_not_answer_fails_on_evidence(self):
        setup = self.make(public_url="https://mine.example",
                          probe=lambda url, timeout: (0, b""))
        state, detail = setup.step_public_url()
        self.assertEqual(state, FAILED)
        self.assertIn("healthz", detail)

    def test_a_blocking_proxy_is_named_as_the_problem(self):
        setup = self.make(public_url="https://mine.example",
                          probe=lambda url, timeout: (403, b""))
        state, detail = setup.step_public_url()
        self.assertEqual(state, FAILED)
        self.assertIn("403", detail)
        self.assertIn("Linear must be able to reach", detail)

    def test_a_missing_url_leaves_the_run_resumable(self):
        result = self.make(probe=lambda url, timeout: (200, b"ok"),
                           doctor_fn=lambda: ([], False)).run()
        states = {step["name"]: step["state"] for step in result["steps"]}
        self.assertEqual(states["public_url"], NEEDS_INPUT)
        self.assertNotEqual(states["app"], SKIPPED, "a missing URL must not abort the run")
        self.assertFalse(result["ok"])


class ResumeTests(SetupBase):
    def test_a_configured_install_is_reported_as_already_done(self):
        self.seed_env(LINEAR_AGENT_PUBLIC_URL="https://example.test",
                      LINEAR_CLIENT_ID="cid", LINEAR_CLIENT_SECRET="csecret",
                      LINEAR_STATE_KEY="s" * 64,
                      LINEAR_WEBHOOK_SECRET="linear-signing")
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
        self.assertEqual(states["state_key"], SKIPPED)
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
                      LINEAR_STATE_KEY="s" * 64,
                      LINEAR_WEBHOOK_SECRET="linear-signing")
        self.seed_token()
        result = self.make(
            transport=FakeTransport(),
            probe=lambda url, timeout: (200, b"ok"),
            doctor_fn=lambda: ([("config", True, "fine")], True),
        ).run()

        self.assertEqual(sorted(result), ["env_file", "ok", "steps"])
        self.assertIsInstance(result["ok"], bool)
        self.assertEqual([s["name"] for s in result["steps"]],
                         ["public_url", "state_key", "app", "bridge", "install", "webhook", "doctor"])
        allowed = {DONE, SKIPPED, NEEDS_INPUT, FAILED}
        for step in result["steps"]:
            self.assertEqual(sorted(step), ["detail", "name", "state"])
            self.assertIn(step["state"], allowed)
            self.assertIsInstance(step["detail"], str)
        self.assertTrue(result["ok"])
        json.dumps(result)   # must serialise: the tab reads it as JSON


class JsonCliTests(SetupBase):
    def test_json_mode_prints_only_json(self):
        """Guidance for humans goes to stderr; stdout has to parse as JSON on its own."""
        import contextlib

        from linear_hermes.plugin_api import main_for_tests

        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            main_for_tests(["setup", "--json", "--yes"])
        payload = json.loads(stdout.getvalue())      # raises if anything else was printed
        self.assertEqual(sorted(payload), ["env_file", "ok", "steps"])
        self.assertIn("--public-url", stderr.getvalue(), "guidance still reaches the human")


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
                         ["public_url", "state_key", "app", "bridge", "install", "webhook"])
        self.assertFalse(state["ok"])
        self.assertEqual({s["state"] for s in state["steps"]}, {"needs_you"})

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
