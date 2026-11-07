import os
import tempfile
import unittest

from linear_hermes.dedupe import DeliveryStore
from linear_hermes.handler import AgentSessionHandler


class FakeLinear:
    def __init__(self):
        self.activities = []

    def post_activity(self, session, kind, body=None, **kw):
        self.activities.append((session, kind, body))


class FakeAdapter:
    def __init__(self, text="done the work", explode=False):
        self.text = text
        self.explode = explode
        self.calls = 0
        self.idempotency_keys = []

    def run(self, prompt, progress=None, idempotency_key=None):
        self.calls += 1
        self.idempotency_keys.append(idempotency_key)
        if self.explode:
            raise RuntimeError("boom")
        return self.text


class SleepyAdapter(FakeAdapter):
    def __init__(self, delay):
        super().__init__()
        self.delay = delay

    def run(self, prompt, progress=None, idempotency_key=None):
        import time

        time.sleep(self.delay)
        return "slow but done"


PAYLOAD = {"type": "AgentSessionEvent", "action": "created",
           "agentSession": {"id": "sess-1"}}


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = DeliveryStore(os.path.join(self.tmp, "s.db"), max_concurrency=1)

    def build(self, adapter=None, store=None):
        self.linear = FakeLinear()
        self.adapter = adapter or FakeAdapter()
        return AgentSessionHandler(self.linear, self.adapter, store or self.store)

    def test_duplicate_delivery_is_dropped(self):
        h = self.build()
        first = h.handle(PAYLOAD, {"Linear-Delivery": "d1"})
        second = h.handle(PAYLOAD, {"Linear-Delivery": "d1"})
        self.assertTrue(first["handled"])
        self.assertFalse(second["handled"])
        self.assertEqual(second["reason"], "duplicate_delivery")

    def test_follow_up_prompt_in_same_session_runs(self):
        """The advertised interaction: replying to the agent continues the session."""
        h = self.build()
        first = h.handle(PAYLOAD, {"Linear-Delivery": "d1"})
        self.assertTrue(first["handled"])
        self.store.release_slot(first["session"])
        follow_up = h.handle(dict(PAYLOAD, action="prompted"), {"Linear-Delivery": "d2"})
        self.assertTrue(follow_up["handled"], "a new delivery in the same session must run")

    def test_retry_of_same_delivery_is_dropped(self):
        h = self.build()
        first = h.handle(PAYLOAD, {"Linear-Delivery": "d1"})
        self.store.release_slot(first["session"])
        again = h.handle(PAYLOAD, {"Linear-Delivery": "d1"})
        self.assertEqual(again["reason"], "duplicate_delivery")

    def test_capacity_refusal_does_not_burn_the_delivery(self):
        """At-capacity tells the user to retry; that retry must not be a duplicate."""
        other = DeliveryStore(os.path.join(self.tmp, "cap.db"), max_concurrency=1)
        other.try_acquire_slot("busy")
        h = self.build(store=other)
        self.assertEqual(h.handle(PAYLOAD, {"Linear-Delivery": "d9"})["reason"], "at_capacity")
        other.release_slot("busy")
        self.assertTrue(h.handle(PAYLOAD, {"Linear-Delivery": "d9"})["handled"])

    def test_non_agent_event_ignored(self):
        h = self.build()
        self.assertFalse(h.handle({"type": "Issue", "action": "update"})["handled"])

    def test_both_actions_handled(self):
        for action in ("created", "prompted"):
            payload = {"type": "AgentSessionEvent", "action": action,
                       "agentSession": {"id": "sess-" + action}}
            fresh = DeliveryStore(os.path.join(self.tmp, "s-%s.db" % action),
                                  max_concurrency=1)
            h = self.build(store=fresh)
            self.assertTrue(h.handle(payload, {"Linear-Delivery": action})["handled"])

    def test_acknowledges_before_running(self):
        """The thought must land inside Linear's 10s window, before adapter work."""
        h = self.build()
        info = h.handle(PAYLOAD, {"Linear-Delivery": "d1"})
        self.assertEqual(self.adapter.calls, 0)          # nothing slow ran yet
        self.assertEqual(self.linear.activities[0][1], "thought")

    def test_happy_path_posts_response_last(self):
        h = self.build()
        info = h.handle(PAYLOAD, {"Linear-Delivery": "d1"})
        h.run_session(info["session"], "do it", idempotency_key="d1")
        kinds = [a[1] for a in self.linear.activities]
        self.assertEqual(kinds, ["thought", "response"])
        self.assertEqual(self.linear.activities[-1][2], "done the work")
        self.assertEqual(self.adapter.idempotency_keys, ["d1"])

    def test_failure_reports_error_and_releases_slot(self):
        h = self.build(adapter=FakeAdapter(explode=True))
        info = h.handle(PAYLOAD, {"Linear-Delivery": "d1"})
        with self.assertRaises(RuntimeError):
            h.run_session(info["session"], "do it")
        self.assertEqual(self.linear.activities[-1][1], "error")
        self.assertEqual(self.store.active_count(), 0)

    def test_capacity_refuses_with_message(self):
        other = DeliveryStore(os.path.join(self.tmp, "s.db"), max_concurrency=1)
        other.try_acquire_slot("busy")
        h = self.build(store=other)
        r = h.handle(PAYLOAD, {"Linear-Delivery": "d9"})
        self.assertEqual(r["reason"], "at_capacity")
        self.assertEqual(self.linear.activities[0][1], "response")

    def test_long_run_emits_one_progress_thought(self):
        self.build()  # initialise the fake collaborators
        h = AgentSessionHandler(
            self.linear, SleepyAdapter(0.25), self.store, heartbeat_seconds=0.05
        )
        info = h.handle(PAYLOAD, {"Linear-Delivery": "d1"})
        h.run_session(info["session"], "do it")
        kinds = [a[1] for a in self.linear.activities]
        self.assertEqual(kinds.count("thought"), 2)   # ack + heartbeat
        self.assertEqual(kinds[-1], "response")

    def test_fast_run_emits_no_heartbeat(self):
        self.build()
        h = AgentSessionHandler(
            self.linear, FakeAdapter(), self.store, heartbeat_seconds=5
        )
        info = h.handle(PAYLOAD, {"Linear-Delivery": "d1"})
        h.run_session(info["session"], "do it")
        self.assertEqual([a[1] for a in self.linear.activities].count("thought"), 1)

    def test_adapter_for_routes_by_payload(self):
        default = FakeAdapter("default answer")
        special = FakeAdapter("special answer")
        linear = FakeLinear()
        h = AgentSessionHandler(
            linear, default, self.store,
            adapter_for=lambda payload: special if "issue" in (payload.get("agentSession") or {}) else default,
        )
        info = h.handle(PAYLOAD, {"Linear-Delivery": "d1"})
        h.run_session(info["session"], "p", payload=dict(PAYLOAD, agentSession={"id": "sess-1", "issue": {}}))
        self.assertEqual(special.calls, 1)
        self.assertEqual(default.calls, 0)
        self.assertEqual(linear.activities[-1][2], "special answer")

    def test_prompt_prefers_linear_prompt_context(self):
        h = self.build()
        payload = dict(PAYLOAD, promptContext="Linear's own formatted context")
        self.assertEqual(self.build().build_prompt(payload),
                         "Linear's own formatted context")


if __name__ == "__main__":
    unittest.main()
