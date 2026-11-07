import json
import unittest

from linear_hermes.linear import (
    ACTION,
    ERROR,
    RESPONSE,
    THOUGHT,
    DeferredLinearClient,
    LinearClient,
    LinearCredentialMissing,
    LinearError,
)

from unittest.mock import patch


class Recorder:
    """Fake transport capturing the GraphQL variables we send."""

    def __init__(self, response=None):
        self.calls = []
        self.response = response or {"data": {"agentActivityCreate": {"success": True,
                                                                     "agentActivity": {"id": "a1"}}}}

    def __call__(self, url, headers, payload, timeout):
        self.calls.append(json.loads(payload.decode()))
        return 200, self.response


class LinearClientTests(unittest.TestCase):
    def build(self, resp=None):
        self.t = Recorder(resp)
        return LinearClient("key", transport=self.t)

    def last_content(self):
        return self.t.calls[-1]["variables"]["input"]["content"]

    def test_thought_uses_body_not_activity_key(self):
        c = self.build()
        c.post_activity("sess", THOUGHT, "thinking about it")
        sent = self.t.calls[-1]
        self.assertEqual(sent["variables"]["input"]["agentSessionId"], "sess")
        content = self.last_content()
        self.assertEqual(content["type"], "thought")
        self.assertEqual(content["body"], "thinking about it")
        # the earlier, wrong shape must not be present
        self.assertNotIn("activity", sent["variables"]["input"])

    def test_response_type(self):
        c = self.build()
        c.post_activity("sess", RESPONSE, "final answer")
        self.assertEqual(self.last_content(), {"type": "response", "body": "final answer"})

    def test_error_type(self):
        c = self.build()
        c.post_activity("sess", ERROR, "boom")
        self.assertEqual(self.last_content()["type"], "error")

    def test_action_requires_action_and_parameter(self):
        c = self.build()
        c.post_activity("sess", ACTION, action="Searching", parameter="auth flow")
        content = self.last_content()
        self.assertEqual(content["type"], "action")
        self.assertEqual(content["action"], "Searching")
        self.assertEqual(content["parameter"], "auth flow")
        self.assertNotIn("result", content)

    def test_action_with_result(self):
        c = self.build()
        c.post_activity("sess", ACTION, action="Searched", parameter="auth flow",
                        result="found 2 matches")
        self.assertEqual(self.last_content()["result"], "found 2 matches")

    def test_success_false_raises(self):
        c = self.build({"data": {"agentActivityCreate": {"success": False}}})
        with self.assertRaises(LinearError):
            c.post_activity("sess", THOUGHT, "x")

    def test_graphql_errors_raise(self):
        c = self.build({"errors": [{"message": "bad shape"}]})
        with self.assertRaises(LinearError):
            c.post_activity("sess", THOUGHT, "x")

    def test_http_error_raises(self):
        def boom(url, headers, payload, timeout):
            return 500, {"error": "http_500"}

        c = LinearClient("key", transport=boom)
        with self.assertRaises(LinearError):
            c.post_activity("sess", THOUGHT, "x")

    def test_missing_key_raises(self):
        with self.assertRaises(LinearError):
            LinearClient("")


class DeferredClientTests(unittest.TestCase):
    """A fresh install has no credential; booting must still succeed."""

    def test_constructs_without_credential(self):
        DeferredLinearClient("run make authorize")  # must not raise

    def test_use_raises_actionable_error(self):
        c = DeferredLinearClient("run make authorize")
        for call in (lambda: c.post_activity("s", "thought", "x"),
                     lambda: c.issue_context("id"),
                     lambda: c.set_external_url("s", "u")):
            with self.assertRaises(LinearCredentialMissing) as ctx:
                call()
            self.assertIn("make authorize", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
