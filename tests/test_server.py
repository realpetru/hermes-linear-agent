import json
import threading
import unittest
import urllib.error
import urllib.request

from linear_hermes.config import Config
from linear_hermes.server import serve
from linear_hermes.signature import compute_signature

SECRET = "topsecret"
BODY = json.dumps({"type": "AgentSessionEvent", "action": "created",
                   "agentSession": {"id": "sess-1"}}).encode()


def make_cfg(port):
    return Config(
        webhook_secret=SECRET, linear_api_key="x", hermes_adapter="subprocess",
        gateway_url="", gateway_token="", hermes_bin="hermes", port=port,
        bind="127.0.0.1", max_concurrency=1, db_path="/tmp/x.sqlite3",
        agent_name="Hermes", log_level="INFO",
    )


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.seen = []
        self.httpd = serve(make_cfg(0), lambda p, h: self.seen.append(p))
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def post(self, body, sig):
        req = urllib.request.Request("http://127.0.0.1:%d/webhook" % self.port, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if sig is not None:
            req.add_header("Linear-Signature", sig)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_unsigned_rejected_and_never_reaches_agent(self):
        self.assertEqual(self.post(BODY, None), 401)
        self.assertEqual(self.seen, [])

    def test_bad_signature_rejected(self):
        self.assertEqual(self.post(BODY, "deadbeef"), 401)
        self.assertEqual(self.seen, [])

    def test_signed_accepted(self):
        self.assertEqual(self.post(BODY, compute_signature(SECRET, BODY)), 200)
        for _ in range(50):
            if self.seen:
                break
            threading.Event().wait(0.02)
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.seen[0]["agentSession"]["id"], "sess-1")

    def test_oauth_callback_without_code_is_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen("http://127.0.0.1:%d/oauth/callback" % self.port, timeout=5)
        self.assertEqual(ctx.exception.code, 400)

    def test_oauth_callback_without_configured_app_is_500(self):
        """No client id/secret yet: say so plainly instead of a 404."""
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(
                "http://127.0.0.1:%d/oauth/callback?code=abc" % self.port, timeout=5
            )
        self.assertEqual(ctx.exception.code, 500)

    def test_oauth_callback_reports_declined_install(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(
                "http://127.0.0.1:%d/oauth/callback?error=access_denied" % self.port, timeout=5
            )
        self.assertEqual(ctx.exception.code, 400)

    def test_health(self):
        with urllib.request.urlopen("http://127.0.0.1:%d/healthz" % self.port, timeout=5) as r:
            self.assertEqual(r.status, 200)


if __name__ == "__main__":
    unittest.main()
