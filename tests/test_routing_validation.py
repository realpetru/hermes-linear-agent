"""A routing-map typo must never reach an adapter.

`/p/<profile>/v1/runs` answers 404 for a profile that does not exist, so an
unvalidated map target turns a routing mistake into a dead session. These tests pin
the fall-through, the single warning, and the wiring `__main__` uses.
"""
import types
import unittest

from linear_hermes.adapters import build_adapter
from linear_hermes.routing import choose_profile


def payload_with_labels(*names):
    return {
        "agentSession": {
            "issue": {"labels": {"nodes": [{"name": name} for name in names]}}
        }
    }


REAL = ["default", "andy", "appy", "cody", "studio"]


class MapTargetTests(unittest.TestCase):
    def test_valid_map_target_still_wins(self):
        got = choose_profile(
            payload_with_labels("bug"),
            agent_profile="appy",
            routing={"bug": "studio"},
            available=REAL,
        )
        self.assertEqual(got, "studio")

    def test_unknown_map_target_falls_through_to_agent_profile(self):
        got = choose_profile(
            payload_with_labels("bug"),
            agent_profile="appy",
            routing={"bug": "typo-profile"},
            available=REAL,
        )
        self.assertEqual(got, "appy", "an unknown target must not be handed to the adapter")

    def test_unknown_map_target_logs_exactly_one_warning(self):
        with self.assertLogs("linear_hermes.routing", level="WARNING") as logs:
            choose_profile(
                payload_with_labels("bug", "cody"),
                agent_profile="appy",
                routing={"bug": "typo-profile"},
                available=REAL,
            )
        self.assertEqual(len(logs.output), 1)
        self.assertIn("typo-profile", logs.output[0])
        self.assertIn("bug", logs.output[0])

    def test_unknown_target_falls_through_to_a_label_named_profile(self):
        # precedence continues: map (rejected) -> label route -> agent_profile
        got = choose_profile(
            payload_with_labels("bug", "cody"),
            agent_profile="appy",
            routing={"bug": "typo-profile"},
            available=REAL,
        )
        self.assertEqual(got, "cody")

    def test_unknown_target_falls_through_to_the_gateway_default(self):
        got = choose_profile(
            payload_with_labels("bug"),
            agent_profile="",
            routing={"bug": "typo-profile"},
            available=REAL,
        )
        self.assertEqual(got, "")

    def test_case_insensitive_target(self):
        got = choose_profile(
            payload_with_labels("bug"),
            routing={"bug": "STUDIO"},
            available=REAL,
        )
        self.assertEqual(got, "studio")

    def test_without_a_profile_list_the_explicit_map_is_honoured(self):
        # Nothing to validate against: an operator's explicit map is trusted rather
        # than silently dropped.
        got = choose_profile(payload_with_labels("bug"), routing={"bug": "typo-profile"}, available=[])
        self.assertEqual(got, "typo-profile")

    def test_every_outcome_is_a_real_profile_or_empty(self):
        routing = {"bug": "typo-profile", "feature": "cody", "nope": "ghost"}
        for label in ("bug", "feature", "nope", "other"):
            got = choose_profile(
                payload_with_labels(label), agent_profile="appy", routing=routing, available=REAL
            )
            self.assertTrue(got == "" or got in REAL, "%s resolved to %r" % (label, got))


class AdapterWiringTests(unittest.TestCase):
    """The exact wiring from __main__: profile_for -> build_adapter(profile=...)."""

    def setUp(self):
        self.cfg = types.SimpleNamespace(
            hermes_adapter="gateway",
            gateway_url="http://127.0.0.1:8642",
            gateway_token="test-token",
            agent_profile="appy",
            routing={"bug": "typo-profile", "feature": "studio"},
        )

    def adapter_for(self, payload):
        profile = choose_profile(
            payload, self.cfg.agent_profile, self.cfg.routing, REAL
        )
        return build_adapter(self.cfg, profile=profile)

    def test_adapter_never_gets_the_unknown_target(self):
        adapter = self.adapter_for(payload_with_labels("bug"))
        self.assertNotEqual(adapter.profile, "typo-profile")
        self.assertEqual(adapter.profile, "appy")
        self.assertNotIn("/p/typo-profile", adapter._base())

    def test_adapter_gets_a_valid_map_target(self):
        adapter = self.adapter_for(payload_with_labels("feature"))
        self.assertEqual(adapter.profile, "studio")
        self.assertIn("/p/studio", adapter._base())


if __name__ == "__main__":
    unittest.main()
