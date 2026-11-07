import unittest

import subprocess
from unittest.mock import patch

from linear_hermes.adapters import AdapterError, GatewayAdapter, SubprocessAdapter


class FakeGateway(GatewayAdapter):
    """Transport-level fake: records requests, replays canned responses."""

    def __init__(self, statuses=None, final=None, create=None, profile=""):
        super().__init__("http://fake", api_key="k", timeout=5, poll_interval=0,
                         profile=profile)
        self.statuses = list(statuses or ["queued", "running", "completed"])
        self.final = final or {"status": "completed", "output": "the answer"}
        self.create = create if create is not None else {"run_id": "run_abc"}
        self.requests = []

    def _request(self, path, payload=None, idempotency_key=None):
        self.requests.append((self._base() + path, payload, idempotency_key))
        if path == "/v1/runs":
            return self.create
        state = {"status": self.statuses.pop(0) if self.statuses else "completed"}
        if state["status"] == "completed":
            state.update(self.final)
        return state


class GatewayAdapterTests(unittest.TestCase):
    def test_posts_input_and_returns_output(self):
        gw = FakeGateway()
        out = gw.run("hello world", idempotency_key="idem-1")
        self.assertEqual(out, "the answer")
        path, payload, key = gw.requests[0]
        self.assertEqual(path, "http://fake/v1/runs")
        self.assertEqual(payload, {"input": "hello world"})
        self.assertEqual(key, "idem-1")

    def test_polls_until_terminal(self):
        gw = FakeGateway(statuses=["queued", "running"])
        self.assertEqual(gw.run("x"), "the answer")
        # create + at least 3 polls
        self.assertGreaterEqual(len(gw.requests), 4)

    def test_accepts_id_field_as_run_id(self):
        gw = FakeGateway(create={"id": "run_xyz"}, statuses=["completed"])
        self.assertEqual(gw.run("x"), "the answer")
        self.assertTrue(any(r[0] == "http://fake/v1/runs/run_xyz" for r in gw.requests))

    def test_missing_run_id_raises(self):
        gw = FakeGateway(create={})
        with self.assertRaises(AdapterError):
            gw.run("x")

    def test_failed_status_raises(self):
        gw = FakeGateway(statuses=["running", "failed"], final={"status": "failed"})
        with self.assertRaises(AdapterError):
            gw.run("x")

    def test_waiting_for_approval_is_not_terminal(self):
        gw = FakeGateway(statuses=["waiting_for_approval", "completed"])
        self.assertEqual(gw.run("x"), "the answer")

    def test_alternate_output_keys(self):
        gw = FakeGateway(statuses=["completed"], final={"status": "completed", "response": "alt"})
        self.assertEqual(gw.run("x"), "alt")

    def test_requires_base_url(self):
        with self.assertRaises(AdapterError):
            GatewayAdapter("")

    def test_no_profile_keeps_the_original_paths(self):
        gw = FakeGateway()
        gw.run("x")
        self.assertEqual(gw.requests[0][0], "http://fake/v1/runs")

    def test_profile_prefixes_the_run_and_status_paths(self):
        gw = FakeGateway(profile="cody")
        gw.run("x")
        self.assertEqual(gw.requests[0][0], "http://fake/p/cody/v1/runs")
        self.assertTrue(all("/p/cody/v1/runs" in r[0] for r in gw.requests))

    def test_profile_is_normalised(self):
        self.assertEqual(GatewayAdapter("http://fake", profile=" CODY ").profile, "cody")


class SubprocessAdapterTests(unittest.TestCase):
    def test_argv_without_profile(self):
        with patch("subprocess.run") as run:
            run.return_value = type("P", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
            self.assertEqual(SubprocessAdapter("hermes").run("hi"), "ok")
            argv = run.call_args[0][0]
        self.assertEqual(argv, ["hermes", "chat", "-q", "hi"])

    def test_argv_with_profile(self):
        with patch("subprocess.run") as run:
            run.return_value = type("P", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
            SubprocessAdapter("hermes", profile="cody").run("hi")
            argv = run.call_args[0][0]
        self.assertEqual(argv, ["hermes", "-p", "cody", "chat", "-q", "hi"])


class BuildAdapterTests(unittest.TestCase):
    def _cfg(self, **kw):
        from linear_hermes.config import Config

        base = dict(
            webhook_secret="s", linear_api_key="", hermes_adapter="gateway",
            gateway_url="http://gw", gateway_token="", hermes_bin="hermes",
            port=1, bind="127.0.0.1", max_concurrency=1, db_path="/tmp/x",
            agent_name="Hermes", log_level="INFO",
        )
        base.update(kw)
        return Config(**base)

    def test_agent_profile_flows_into_gateway_adapter(self):
        from linear_hermes.adapters import build_adapter

        self.assertEqual(build_adapter(self._cfg(agent_profile="cody")).profile, "cody")

    def test_explicit_profile_overrides_config(self):
        from linear_hermes.adapters import build_adapter

        self.assertEqual(build_adapter(self._cfg(agent_profile="cody"), profile="appy").profile, "appy")

    def test_no_profile_leaves_gateway_default(self):
        from linear_hermes.adapters import build_adapter

        self.assertEqual(build_adapter(self._cfg()).profile, "")


if __name__ == "__main__":
    unittest.main()
