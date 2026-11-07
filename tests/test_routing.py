import unittest

from linear_hermes.routing import choose_profile, issue_labels


def payload_with_labels(*names):
    return {
        "type": "AgentSessionEvent",
        "action": "created",
        "agentSession": {"id": "s1", "issue": {"labels": {"nodes": [{"name": n} for n in names]}}},
    }


class RoutingTests(unittest.TestCase):
    def test_labels_extracted_from_nodes(self):
        self.assertEqual(issue_labels(payload_with_labels("Cody", "bug")), ["cody", "bug"])

    def test_labels_extracted_from_flat_list(self):
        payload = {"agentSession": {"issue": {"labels": ["cody"]}}}
        self.assertEqual(issue_labels(payload), ["cody"])

    def test_no_labels_is_empty(self):
        self.assertEqual(issue_labels({"agentSession": {"id": "s"}}), [])

    def test_label_route_beats_agent_profile(self):
        got = choose_profile(payload_with_labels("cody"), agent_profile="appy",
                             routing={"cody": "cody"})
        self.assertEqual(got, "cody")

    def test_agent_profile_used_when_no_label_matches(self):
        got = choose_profile(payload_with_labels("bug"), agent_profile="appy",
                             routing={"cody": "cody"})
        self.assertEqual(got, "appy")

    def test_empty_when_nothing_configured(self):
        self.assertEqual(choose_profile(payload_with_labels("cody")), "")

    def test_label_match_is_case_insensitive(self):
        got = choose_profile(payload_with_labels("CODY"), routing={"cody": "studio"})
        self.assertEqual(got, "studio")

    def test_first_matching_route_wins_in_config_order(self):
        routing = {"cody": "cody", "bug": "appy"}
        self.assertEqual(choose_profile(payload_with_labels("cody", "bug"), routing=routing), "cody")

    def test_none_payload_is_safe(self):
        self.assertEqual(choose_profile(None, agent_profile="appy"), "appy")


if __name__ == "__main__":
    unittest.main()
