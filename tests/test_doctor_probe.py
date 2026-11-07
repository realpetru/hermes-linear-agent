"""The doctor's own probes must look like a client, not like Python-urllib.

Cloudflare (and most CDN bot rules) answer 403 to ``Python-urllib/x`` — the default of
``urllib.request`` — so a probe with no User-Agent reports "cannot reach this" on a
healthy install behind a tunnel. These tests pin the header and pin how a 403 is worded,
because "blocked at the edge" and "nothing answered" are different problems.
"""
import io
import unittest
import urllib.request

from linear_hermes import plugin_api


class _FakeResponse(io.BytesIO):
    def __init__(self, body=b"{}", status=200):
        super().__init__(body)
        self._status = status

    def getcode(self):
        return self._status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class ProbeHeaderTests(unittest.TestCase):
    def setUp(self):
        self._urlopen = urllib.request.urlopen
        self.seen = []

    def tearDown(self):
        urllib.request.urlopen = self._urlopen

    def capture(self, status=200):
        def fake_urlopen(request, timeout=None):
            self.seen.append(request)
            if status >= 400:
                raise urllib.error.HTTPError(
                    request.full_url, status, "Forbidden", {}, io.BytesIO(b"blocked")
                )
            return _FakeResponse()

        urllib.request.urlopen = fake_urlopen

    def test_probe_sends_a_user_agent(self):
        self.capture()
        plugin_api._probe("https://example.invalid/healthz")
        self.assertEqual(len(self.seen), 1)
        agent = self.seen[0].get_header("User-agent")
        self.assertTrue(agent, "no User-Agent means Cloudflare answers 403")
        self.assertIn("linear-agent-doctor/", agent)
        self.assertNotIn("Python-urllib", agent)

    def test_probe_keeps_an_explicit_user_agent(self):
        self.capture()
        plugin_api._probe("https://example.invalid/healthz", headers={"User-Agent": "curl/8"})
        self.assertEqual(self.seen[0].get_header("User-agent"), "curl/8")

    def test_probe_still_sends_caller_headers(self):
        self.capture()
        plugin_api._probe("https://example.invalid/webhook", headers={"Linear-Signature": "abc"})
        self.assertEqual(self.seen[0].get_header("Linear-signature"), "abc")

    def test_user_agent_carries_the_version(self):
        self.assertIn(plugin_api._version(), plugin_api.user_agent())

    def test_bridge_status_also_sends_a_user_agent(self):
        import json as _json

        def fake_urlopen(request, timeout=None):
            self.seen.append(request)
            return _FakeResponse(_json.dumps({"status": "ok"}).encode())

        urllib.request.urlopen = fake_urlopen
        plugin_api.bridge_status("http://127.0.0.1:8650")
        self.assertEqual(len(self.seen), 1)
        self.assertIn("linear-agent-doctor/", self.seen[0].get_header("User-agent") or "")


import urllib.error  # noqa: E402  (used by capture())


class WordingTests(unittest.TestCase):
    """A 403 from a proxy must not read as "the bridge is unreachable"."""

    def checks(self, probe):
        original = plugin_api._probe
        plugin_api._probe = probe
        try:
            import os
            import tempfile

            home = tempfile.mkdtemp()
            keys = ("HERMES_HOME", "LINEAR_AGENT_SETTINGS_DIR", "LINEAR_TOKEN_PATH",
                    "LINEAR_CLIENT_ID", "LINEAR_CLIENT_SECRET", "LINEAR_WEBHOOK_SECRET",
                    "LINEAR_AGENT_PUBLIC_URL")
            saved = {k: os.environ.get(k) for k in keys}
            os.environ["HERMES_HOME"] = home
            os.environ["LINEAR_AGENT_SETTINGS_DIR"] = os.path.join(home, "s")
            for key, value in (
                ("LINEAR_CLIENT_ID", "c"), ("LINEAR_CLIENT_SECRET", "cs"),
                ("LINEAR_WEBHOOK_SECRET", "ws"), ("LINEAR_AGENT_PUBLIC_URL", "https://host.example"),
            ):
                os.environ[key] = value
            from linear_hermes.oauth import save_tokens
            import pathlib

            path = os.path.join(home, "s", "linear-tokens.json")
            pathlib.Path(home, "s").mkdir(parents=True, exist_ok=True)
            save_tokens(path, {"access_token": "t", "expires_at": "2030-01-01T00:00:00+00:00"})
            os.environ["LINEAR_TOKEN_PATH"] = path
            bridge = plugin_api.bridge_status
            plugin_api.bridge_status = lambda *a, **k: {"reachable": True, "url": "http://127.0.0.1:8650"}
            try:
                result, _ok = plugin_api.doctor(probe=probe)
            finally:
                plugin_api.bridge_status = bridge
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            return {name: (passed, detail) for name, passed, detail in result}
        finally:
            plugin_api._probe = original

    def test_403_names_the_proxy_not_the_bridge(self):
        def probe(url, timeout=5, headers=None, data=None, method=None):
            if "api.linear.app" in url:
                return 200, '{"data":{"viewer":{"organization":{"name":"Acme"}}}}'
            if "host.example" in url or (url and "127.0.0.1" in url and url.endswith("/v1/models")):
                return 403, "blocked"
            return 200, "{}"

        checks = self.checks(probe)
        self.assertFalse(checks["public"][0])
        self.assertIn("proxy/CDN", checks["public"][1])
        self.assertIn("not by the bridge", checks["public"][1])
        self.assertFalse(checks["signature"][0])
        self.assertIn("proxy/CDN", checks["signature"][1])
        self.assertIn("not a signature", checks["signature"][1])

    def test_silence_still_reads_as_unreachable(self):
        def probe(url, timeout=5, headers=None, data=None, method=None):
            if "api.linear.app" in url:
                return 200, '{"data":{"viewer":{"organization":{"name":"Acme"}}}}'
            if "host.example" in url:
                return 0, "timed out"
            return 200, "{}"

        checks = self.checks(probe)
        self.assertIn("answered nothing", checks["public"][1])
        self.assertNotIn("proxy/CDN", checks["public"][1])

    def test_a_healthy_probe_is_ok(self):
        def probe(url, timeout=5, headers=None, data=None, method=None):
            if "api.linear.app" in url:
                return 200, '{"data":{"viewer":{"organization":{"name":"Acme"}}}}'
            return 200, "{}"

        checks = self.checks(probe)
        self.assertTrue(checks["public"][0], checks["public"])
        self.assertTrue(checks["signature"][0], checks["signature"])


if __name__ == "__main__":
    unittest.main()
