import unittest

from linear_hermes.adapters import AdapterError, GatewayAdapter


class FakeGateway(GatewayAdapter):
    """Transport-level fake: records requests, replays canned responses."""

    def __init__(self, statuses=None, final=None, create=None):
        super().__init__("http://fake", api_key="k", timeout=5, poll_interval=0)
        self.statuses = list(statuses or ["queued", "running", "completed"])
        self.final = final or {"status": "completed", "output": "the answer"}
        self.create = create if create is not None else {"run_id": "run_abc"}
        self.requests = []

    def _request(self, path, payload=None, idempotency_key=None):
        self.requests.append((path, payload, idempotency_key))
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
        self.assertEqual(path, "/v1/runs")
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
        self.assertTrue(any(r[0] == "/v1/runs/run_xyz" for r in gw.requests))

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


if __name__ == "__main__":
    unittest.main()
