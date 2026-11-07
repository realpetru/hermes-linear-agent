"""doctor: one command that proves (or disproves) an install."""
import json
import os
import pathlib
import tempfile
import unittest

from linear_hermes import plugin_api
from linear_hermes.oauth import save_tokens


class _DoctorBase(object):  # mixin: unittest must not collect it directly
    # pylint: disable=no-member
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.settings_dir = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in (
            "HERMES_HOME", "LINEAR_AGENT_SETTINGS_DIR", "LINEAR_TOKEN_PATH",
            "LINEAR_CLIENT_ID", "LINEAR_CLIENT_SECRET", "LINEAR_WEBHOOK_SECRET",
            "LINEAR_AGENT_PUBLIC_URL", "LINEAR_REDIRECT_URI", "LINEAR_AGENT_PROFILE",
            "HERMES_GATEWAY_URL", "HERMES_GATEWAY_TOKEN", "LINEAR_AGENT_ROUTING",
        )}
        os.environ["HERMES_HOME"] = self.home
        os.environ["LINEAR_AGENT_SETTINGS_DIR"] = self.settings_dir
        os.environ.pop("LINEAR_TOKEN_PATH", None)
        os.environ["LINEAR_CLIENT_ID"] = "cid"
        os.environ["LINEAR_CLIENT_SECRET"] = "csecret"
        os.environ["LINEAR_WEBHOOK_SECRET"] = "wsecret"
        os.environ["LINEAR_AGENT_PUBLIC_URL"] = "https://host.example"
        os.environ["HERMES_GATEWAY_URL"] = "http://127.0.0.1:8642"
        os.environ["HERMES_GATEWAY_TOKEN"] = "gwtoken"
        self._bridge = plugin_api.bridge_status
        plugin_api.bridge_status = lambda url=None, timeout=3.0, **kw: {
            "reachable": True, "url": "http://127.0.0.1:8650", "version": "1.3.1"
        }

    def tearDown(self):
        plugin_api.bridge_status = self._bridge
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def token_file(self):
        return os.path.join(self.settings_dir, "linear-tokens.json")

    def write_token(self):
        save_tokens(self.token_file(), {"access_token": "app-token", "refresh_token": "r", "expires_at": "2030-01-01T00:00:00+00:00"})
        os.environ["LINEAR_TOKEN_PATH"] = self.token_file()

    def probe_all_ok(self, url, timeout=5, headers=None, data=None, method=None):
        if "api.linear.app" in url:
            return 200, json.dumps({"data": {"viewer": {"organization": {"name": "Acme"}}}})
        if url.endswith("/webhook"):
            return 200, "{}"
        return 200, "{}"

    def checks(self, probe=None):
        checks, ok = plugin_api.doctor(probe=probe or self.probe_all_ok)
        return {name: (passed, detail) for name, passed, detail in checks}, ok

    def test_everything_ok(self):
        self.write_token()
        checks, ok = self.checks()
        self.assertTrue(ok, checks)
        for name in ("config", "token", "bridge", "public", "signature", "gateway"):
            self.assertTrue(checks[name][0], "%s: %s" % (name, checks[name][1]))

    def test_missing_credentials_fail_config(self):
        os.environ.pop("LINEAR_CLIENT_SECRET")
        checks, ok = self.checks()
        self.assertFalse(ok)
        self.assertFalse(checks["config"][0])
        self.assertIn("LINEAR_CLIENT_SECRET", checks["config"][1])

    def test_missing_public_url_fails(self):
        self.write_token()
        os.environ.pop("LINEAR_AGENT_PUBLIC_URL")
        checks, _ok = self.checks()
        self.assertFalse(checks["config"][0])
        self.assertFalse(checks["public"][0])

    def test_no_token_says_how_to_get_one(self):
        checks, ok = self.checks()
        self.assertFalse(ok)
        self.assertFalse(checks["token"][0])
        self.assertIn("authorize", checks["token"][1])

    def test_token_rejected_by_linear(self):
        self.write_token()

        def probe(url, timeout=5, headers=None, data=None, method=None):
            if "api.linear.app" in url:
                return 401, "{}"
            return 200, "{}"

        checks, ok = self.checks(probe)
        self.assertFalse(ok)
        self.assertFalse(checks["token"][0])
        self.assertIn("rejected", checks["token"][1])

    def test_offline_linear_does_not_fail_the_token_check(self):
        self.write_token()

        def probe(url, timeout=5, headers=None, data=None, method=None):
            if "api.linear.app" in url:
                return 0, "timed out"
            return 200, "{}"

        checks, ok = self.checks(probe)
        self.assertTrue(checks["token"][0], checks["token"])
        self.assertTrue(ok)

    def test_bad_signature_is_reported_as_such(self):
        self.write_token()

        def probe(url, timeout=5, headers=None, data=None, method=None):
            if "api.linear.app" in url:
                return 200, json.dumps({"data": {"viewer": {"organization": {"name": "Acme"}}}})
            if url.endswith("/webhook"):
                return 401, "{}"
            return 200, "{}"

        checks, ok = self.checks(probe)
        self.assertFalse(ok)
        self.assertFalse(checks["signature"][0])
        self.assertIn("LINEAR_WEBHOOK_SECRET", checks["signature"][1])

    def test_unreachable_public_host(self):
        self.write_token()

        def probe(url, timeout=5, headers=None, data=None, method=None):
            if "api.linear.app" in url:
                return 200, json.dumps({"data": {"viewer": {"organization": {"name": "Acme"}}}})
            if "host.example" in url:
                return 0, "Name or service not known"
            return 200, "{}"

        checks, ok = self.checks(probe)
        self.assertFalse(ok)
        self.assertFalse(checks["public"][0])
        self.assertIn("cannot reach", checks["public"][1])

    def test_gateway_401_means_token_mismatch(self):
        self.write_token()

        def probe(url, timeout=5, headers=None, data=None, method=None):
            if "api.linear.app" in url:
                return 200, json.dumps({"data": {"viewer": {"organization": {"name": "Acme"}}}})
            if url.endswith("/v1/models"):
                return 401, "{}"
            return 200, "{}"

        checks, _ok = self.checks(probe)
        self.assertFalse(checks["gateway"][0])
        self.assertIn("API_SERVER_KEY", checks["gateway"][1])

    def test_bridge_down(self):
        self.write_token()
        plugin_api.bridge_status = lambda url=None, timeout=3.0, **kw: {
            "reachable": False, "error": "connection refused", "url": "http://127.0.0.1:8650"
        }
        checks, ok = self.checks()
        self.assertFalse(ok)
        self.assertFalse(checks["bridge"][0])
        self.assertIn("serve", checks["bridge"][1])

    def test_doctor_never_prints_secret_values(self):
        self.write_token()
        checks, _ok = self.checks()
        blob = json.dumps(checks)
        for secret in ("csecret", "wsecret", "gwtoken", "app-token"):
            self.assertNotIn(secret, blob)


class DoctorTests(_DoctorBase, unittest.TestCase):
    pass


class DoctorCliTests(_DoctorBase, unittest.TestCase):
    def run_cli(self, argv):
        parser = __import__("argparse").ArgumentParser()
        plugin_api._setup_cli(parser)
        args = parser.parse_args(argv)
        return plugin_api._handle_cli(args)

    def with_doctor(self, result):
        original = plugin_api.doctor
        plugin_api.doctor = lambda **kw: result
        try:
            return self.run_cli(["doctor"])
        finally:
            plugin_api.doctor = original

    def test_exit_code_zero_when_ok(self):
        self.write_token()
        self.assertEqual(self.with_doctor(([("config", True, "ok")], True)), 0)

    def test_exit_code_one_when_a_check_fails(self):
        self.assertEqual(self.with_doctor(([("config", False, "missing")], False)), 1)

    def test_reported_version_matches_the_manifest(self):
        import re

        manifest = (pathlib.Path(__file__).resolve().parents[1] / "plugin.yaml").read_text()
        expected = re.search(r"^version:\s*(\S+)", manifest, re.M).group(1)
        self.assertEqual(plugin_api._version(), expected)

    def test_status_is_rich_not_just_a_probe(self):
        self.write_token()
        doc = plugin_api.status_document()
        for key in ("credentials", "token", "urls", "bridge", "agent_profile", "version"):
            self.assertIn(key, doc)
        self.assertEqual(doc["token"]["present"], True)
        self.assertEqual(doc["urls"]["webhook_url"], "https://host.example/webhook")


if __name__ == "__main__":
    unittest.main()
