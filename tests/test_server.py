import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from linear_hermes.config import Config
from linear_hermes.server import serve
from linear_hermes.signature import compute_signature

import local_endpoints

SECRET = "topsecret"
BODY = json.dumps({"type": "AgentSessionEvent", "action": "created",
                   "agentSession": {"id": "sess-1"}}).encode()


def make_cfg(port, **over):
    base = dict(
        webhook_secret=SECRET, linear_api_key="x", hermes_adapter="subprocess",
        gateway_url="", gateway_token="", hermes_bin="hermes", port=port,
        bind="127.0.0.1", max_concurrency=1, db_path="/tmp/x.sqlite3",
        agent_name="Hermes", log_level="INFO",
    )
    base.update(over)
    return Config(**base)


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
        req = urllib.request.Request(local_endpoints.url(self.port, "/webhook"), data=body, method="POST")
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
            urllib.request.urlopen(local_endpoints.url(self.port, "/oauth/callback"), timeout=5)
        self.assertEqual(ctx.exception.code, 400)

    def test_oauth_callback_without_state_is_rejected(self):
        """A state-changing GET with no CSRF state must not exchange anything."""
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(
                local_endpoints.url(self.port, "/oauth/callback?code=abc"), timeout=5
            )
        self.assertEqual(ctx.exception.code, 400)

    def test_oauth_callback_with_forged_state_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(
                local_endpoints.url(self.port, "/oauth/callback?code=abc&state=1.deadbeef"), timeout=5
            )
        self.assertEqual(ctx.exception.code, 400)

    def test_oauth_callback_without_configured_app_is_500(self):
        """Past the CSRF gate but no client id configured: say so, do not 404."""
        from linear_hermes.oauth import make_state

        state = make_state(SECRET)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(
                local_endpoints.url(self.port, "/oauth/callback?code=abc&state=" + state),
                timeout=5,
            )
        self.assertEqual(ctx.exception.code, 500)

    def test_oauth_callback_reports_declined_install(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(
                local_endpoints.url(self.port, "/oauth/callback?error=access_denied"), timeout=5
            )
        self.assertEqual(ctx.exception.code, 400)

    def test_valid_state_gets_past_the_csrf_gate(self):
        """With a good state the request reaches configuration, i.e. 500 not 400."""
        from linear_hermes.oauth import make_state

        state = make_state(SECRET)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(
                local_endpoints.url(self.port, "/oauth/callback?code=abc&state=" + state),
                timeout=5,
            )
        self.assertEqual(ctx.exception.code, 500)  # no client id configured here

    def test_status_is_minimal_for_anonymous_callers(self):
        """/status is public; it must not leak session counts or which keys are set."""
        import json as _json

        with urllib.request.urlopen(local_endpoints.url(self.port, "/status"), timeout=5) as r:
            body = _json.loads(r.read().decode())
        self.assertEqual(body.get("status"), "ok")
        for leak in ("sessions_handled", "last_error", "agent_profile", "configured"):
            self.assertNotIn(leak, body, "anonymous /status leaked %s" % leak)

    def test_status_returns_detail_with_the_token(self):
        with mock.patch.dict(os.environ, {"STATUS_TOKEN": "s3cret"}), \
             mock.patch.dict(sys.modules, {"agent": None, "agent.secret_scope": None}):
            req = urllib.request.Request(local_endpoints.url(self.port, "/status"))
            req.add_header("Authorization", "Bearer s3cret")
            with urllib.request.urlopen(req, timeout=5) as r:
                body = json.loads(r.read().decode())
        self.assertIn("sessions_handled", body)
        self.assertEqual(body.get("status"), "ok")

    def test_status_rejects_a_wrong_token(self):
        with mock.patch.dict(os.environ, {"STATUS_TOKEN": "s3cret"}), \
             mock.patch.dict(sys.modules, {"agent": None, "agent.secret_scope": None}):
            req = urllib.request.Request(local_endpoints.url(self.port, "/status"))
            req.add_header("Authorization", "Bearer wrong")
            with urllib.request.urlopen(req, timeout=5) as r:
                body = json.loads(r.read().decode())
        self.assertNotIn("sessions_handled", body)

    def test_status_token_can_come_from_the_named_credentials_file(self):
        """Standalone installs name a file; the token is read through read_setting."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "credentials.env")
            with open(path, "w") as fh:
                fh.write("STATUS_TOKEN=named-file-token\n")
            with mock.patch.dict(os.environ, {"LINEAR_AGENT_ENV_FILE": path}), \
                 mock.patch.dict(sys.modules, {"agent": None, "agent.secret_scope": None}):
                os.environ.pop("STATUS_TOKEN", None)
                req = urllib.request.Request(local_endpoints.url(self.port, "/status"))
                req.add_header("Authorization", "Bearer named-file-token")
                with urllib.request.urlopen(req, timeout=5) as r:
                    body = json.loads(r.read().decode())
        self.assertIn("sessions_handled", body)

    def test_health(self):
        with urllib.request.urlopen(local_endpoints.url(self.port, "/healthz"), timeout=5) as r:
            self.assertEqual(r.status, 200)


if __name__ == "__main__":
    unittest.main()


class ReplayWindowTests(unittest.TestCase):
    """LINEAR_MAX_SKEW_SECONDS is either implemented or must not be documented."""

    def setUp(self):
        import json as _json
        import time as _time

        from linear_hermes.signature import compute_signature

        self.seen = []
        self.httpd = serve(make_cfg(0, max_skew_seconds=60), lambda p, h: self.seen.append(p))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        body = _json.dumps({"type": "AgentSessionEvent", "action": "created",
                            "agentSession": {"id": "s1"},
                            "webhookTimestamp": int((_time.time() - 600) * 1000)}).encode()
        self.body = body
        self.sig = compute_signature(SECRET, body)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_stale_delivery_rejected_when_window_enabled(self):
        req = urllib.request.Request(local_endpoints.url(self.port, "/webhook"),
                                     data=self.body, method="POST")
        req.add_header("Linear-Signature", self.sig)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 401)
        self.assertEqual(self.seen, [])
