"""Label routing must reach Linear: the webhook payload has no labels.

Linear's AgentSessionEvent carries the issue id but not its labels, so routing that reads
only the payload always falls through to the configured default agent. These tests pin
the lookup, the cache, the fallbacks, and the wiring the bridge uses.
"""
import unittest

from linear_hermes.routing import choose_profile, resolve_labels

REAL = ["default", "appy", "cody", "studio"]

PAYLOAD_WITHOUT_LABELS = {
    "type": "AgentSessionEvent",
    "action": "created",
    "agentSession": {"id": "sess-1", "issue": {"id": "issue-1", "identifier": "ENG-1"}},
}


def payload_with_labels(*names):
    return {
        "agentSession": {
            "issue": {"id": "issue-2", "labels": {"nodes": [{"name": n} for n in names]}}
        }
    }


class FetchTests(unittest.TestCase):
    def test_labels_are_fetched_when_the_payload_has_none(self):
        calls = []

        def fetch(issue_id):
            calls.append(issue_id)
            return ["cody"]

        labels = resolve_labels(PAYLOAD_WITHOUT_LABELS, fetch=fetch)
        self.assertEqual(labels, ["cody"])
        self.assertEqual(calls, ["issue-1"], "the issue id from the payload is what we query")

    def test_this_would_have_routed_to_the_default_before(self):
        """The regression: with no labels in the payload the profile always lost."""
        fetch = lambda issue_id: ["cody"]  # noqa: E731
        labels = resolve_labels(PAYLOAD_WITHOUT_LABELS, fetch=fetch)
        routed = choose_profile(
            PAYLOAD_WITHOUT_LABELS, agent_profile="appy", routing={}, available=REAL, labels=labels
        )
        self.assertEqual(routed, "cody")

    def test_payload_labels_win_and_no_call_is_made(self):
        def fetch(issue_id):  # pragma: no cover - must not run
            raise AssertionError("Linear should not be queried when the payload has labels")

        labels = resolve_labels(payload_with_labels("studio"), fetch=fetch)
        self.assertEqual(labels, ["studio"])

    def test_cache_means_one_call_per_issue(self):
        calls = []

        def fetch(issue_id):
            calls.append(issue_id)
            return ["cody"]

        cache = {}
        for _ in range(3):
            self.assertEqual(
                resolve_labels(PAYLOAD_WITHOUT_LABELS, fetch=fetch, cache=cache), ["cody"]
            )
        self.assertEqual(len(calls), 1)

    def test_api_failure_falls_through_without_raising(self):
        def fetch(issue_id):
            raise RuntimeError("linear HTTP 500")

        with self.assertLogs("linear_hermes.routing", level="WARNING") as logs:
            labels = resolve_labels(PAYLOAD_WITHOUT_LABELS, fetch=fetch)
        self.assertEqual(labels, [])
        self.assertIn("issue-1", logs.output[0])
        routed = choose_profile(
            PAYLOAD_WITHOUT_LABELS, agent_profile="appy", routing={}, available=REAL, labels=labels
        )
        self.assertEqual(routed, "appy")

    def test_missing_credential_falls_through(self):
        from linear_hermes.linear import LinearCredentialMissing

        def fetch(issue_id):
            raise LinearCredentialMissing("no Linear credential yet")

        self.assertEqual(resolve_labels(PAYLOAD_WITHOUT_LABELS, fetch=fetch), [])

    def test_no_issue_id_means_no_call(self):
        def fetch(issue_id):  # pragma: no cover
            raise AssertionError("nothing to query")

        self.assertEqual(resolve_labels({"agentSession": {}}, fetch=fetch), [])

    def test_names_are_normalised_and_deduplicated(self):
        fetch = lambda issue_id: ["  CODY ", {"name": "Cody"}, "studio", None]  # noqa: E731
        self.assertEqual(resolve_labels(PAYLOAD_WITHOUT_LABELS, fetch=fetch), ["cody", "studio"])

    def test_cache_is_bounded(self):
        cache = {}
        for n in range(600):
            resolve_labels(
                {"agentSession": {"issue": {"id": "issue-%d" % n}}},
                fetch=lambda i: ["cody"],
                cache=cache,
            )
        self.assertLessEqual(len(cache), 500)



class ClientMethodTests(unittest.TestCase):
    """The real client method, over a stubbed transport (no network)."""

    def test_issue_label_names_queries_and_parses(self):
        from linear_hermes.linear import LinearClient

        seen = {}

        def transport(url, headers, payload, timeout):
            seen["body"] = payload
            seen["headers"] = headers
            data = {"data": {"issue": {"labels": {"nodes": [{"name": "cody"}, {"name": "urgent"}]}}}}
            return 200, data

        client = LinearClient("app-token", transport=transport)
        self.assertEqual(client.issue_label_names("issue-9"), ["cody", "urgent"])
        body = seen["body"].decode()
        self.assertIn("issue", body)
        self.assertIn("issue-9", body)
        self.assertIn("labels", body)
        self.assertEqual(seen["headers"]["Authorization"], "app-token")

    def test_issue_label_names_tolerates_an_issue_without_labels(self):
        from linear_hermes.linear import LinearClient

        client = LinearClient("app-token", transport=lambda *a: (200, {"data": {"issue": {}}}))
        self.assertEqual(client.issue_label_names("issue-10"), [])


class WiringTests(unittest.TestCase):
    def test_bridge_wiring_routes_by_fetched_labels(self):
        """Mirrors __main__.profile_for: resolve through the client, then choose."""
        cache = {}

        class Client:
            calls = 0

            def issue_label_names(self, issue_id):
                Client.calls += 1
                return ["cody"]

        client = Client()

        def profile_for(payload, agent_profile="appy", routing=None):
            labels = resolve_labels(payload, fetch=client.issue_label_names, cache=cache)
            return choose_profile(payload, agent_profile, routing, REAL, labels=labels)

        self.assertEqual(profile_for(PAYLOAD_WITHOUT_LABELS), "cody")
        self.assertEqual(profile_for(PAYLOAD_WITHOUT_LABELS), "cody")
        self.assertEqual(Client.calls, 1, "a reply in the same session must reuse the lookup")

    def test_explicit_rule_still_beats_a_fetched_label(self):
        cache = {}
        labels = resolve_labels(PAYLOAD_WITHOUT_LABELS, fetch=lambda i: ["cody"], cache=cache)
        self.assertEqual(
            choose_profile(PAYLOAD_WITHOUT_LABELS, "appy", {"urgent": "studio"}, REAL, labels=labels),
            "cody",
            "no matching label in the map -> the label that names a profile wins",
        )
        labels = resolve_labels(
            {"agentSession": {"issue": {"id": "issue-3"}}}, fetch=lambda i: ["urgent"], cache=cache
        )
        self.assertEqual(choose_profile({}, "appy", {"urgent": "studio"}, REAL, labels=labels), "studio")


if __name__ == "__main__":
    unittest.main()
