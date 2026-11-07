import unittest

from linear_hermes.config import ConfigError, load_config

BASE = {"LINEAR_WEBHOOK_SECRET": "s"}


class ConfigProfileTests(unittest.TestCase):
    def test_profile_defaults_empty(self):
        self.assertEqual(load_config(dict(BASE)).agent_profile, "")

    def test_profile_parsed_and_normalised(self):
        cfg = load_config(dict(BASE, LINEAR_AGENT_PROFILE=" CODY "))
        self.assertEqual(cfg.agent_profile, "cody")

    def test_routing_defaults_empty(self):
        self.assertEqual(load_config(dict(BASE)).routing, {})

    def test_routing_parsed_from_json(self):
        cfg = load_config(dict(BASE, LINEAR_AGENT_ROUTING='{"Cody": "cody", "bug": "APPY"}'))
        self.assertEqual(cfg.routing, {"cody": "cody", "bug": "appy"})

    def test_routing_bad_json_is_a_clear_config_error(self):
        with self.assertRaises(ConfigError) as ctx:
            load_config(dict(BASE, LINEAR_AGENT_ROUTING="{not json"))
        self.assertIn("LINEAR_AGENT_ROUTING", str(ctx.exception))

    def test_routing_must_be_an_object(self):
        with self.assertRaises(ConfigError):
            load_config(dict(BASE, LINEAR_AGENT_ROUTING='["cody"]'))

    def test_redacted_reports_profile_and_routing(self):
        cfg = load_config(dict(BASE, LINEAR_AGENT_PROFILE="cody",
                               LINEAR_AGENT_ROUTING='{"bug": "appy"}'))
        red = cfg.redacted()
        self.assertEqual(red["agent_profile"], "cody")
        self.assertEqual(red["routing"], {"bug": "appy"})

    def test_redacted_hides_gateway_default_clearly(self):
        self.assertEqual(load_config(dict(BASE)).redacted()["agent_profile"], "(gateway default)")


if __name__ == "__main__":
    unittest.main()
