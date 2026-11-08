"""Minimal always-on HTTP receiver. stdlib only, no framework."""
from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .signature import HEADER, verify_signature

log = logging.getLogger(__name__)

SERVICE_VERSION = "1.4.3"
MAX_BODY = 1 << 20  # 1 MiB: Linear payloads are far smaller


def status_token():
    return (os.environ.get("STATUS_TOKEN") or "").strip()


def status_authorized(headers):
    """True only when STATUS_TOKEN is configured and the request presents it."""
    token = status_token()
    if not token:
        return False
    presented = (headers.get("Authorization") or "").strip()
    if not presented.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(presented[7:].strip(), token)


def status_info():
    """Small, non-sensitive status payload for the plugin's status tool."""
    from . import runtime_status

    info = {"status": "ok", "service": "linear-agent", "version": SERVICE_VERSION}
    info.update(runtime_status.snapshot())
    info["agent_profile"] = os.environ.get("LINEAR_AGENT_PROFILE", "").strip().lower() or "(gateway default)"
    info["configured"] = {
        "webhook_secret": bool(os.environ.get("LINEAR_WEBHOOK_SECRET")),
        "client_id": bool(os.environ.get("LINEAR_CLIENT_ID")),
        "app_token": bool(os.environ.get("LINEAR_TOKEN_PATH")),
    }
    return info


def make_handler(cfg, on_event):
    """on_event(payload, headers) runs in a worker thread after a 200 is sent."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "hermes-linear-agent/1.4.3"

        def _send(self, code, body):
            raw = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _html(self, code, title, detail):
            body = ("<html><body style=\"font-family:system-ui;padding:2rem\">"
                    "<h2>%s</h2><p>%s</p></body></html>" % (title, detail)).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _oauth_callback(self):
            """Finishing the install must be visible, not a 404.

            This is a state-changing GET, so it verifies the signed ``state`` value
            that ``make authorize`` / the authorize tool minted. Without it anyone
            could replay an arbitrary code into the token store.
            """
            query = parse_qs(urlparse(self.path).query)
            code = (query.get("code") or [""])[0]
            error = (query.get("error") or [""])[0]
            state = (query.get("state") or [""])[0]
            from .oauth import verify_state

            if not verify_state(cfg.state_key or cfg.webhook_secret, state):
                log.warning("oauth callback rejected: missing or stale state")
                return self._html(400, "Install link expired",
                                  "Open the URL from `make authorize` again (links last 15 minutes).")
            if error:
                return self._html(400, "Install declined", "Linear returned: %s" % error)
            if not code:
                return self._html(400, "No code", "This URL needs a ?code= parameter.")
            if not (cfg.linear_client_id and cfg.linear_client_secret):
                return self._html(500, "Not configured",
                                  "LINEAR_CLIENT_ID / LINEAR_CLIENT_SECRET are missing on the server.")
            try:
                from .linear import urllib_transport
                from .oauth import exchange_code, save_tokens

                record = exchange_code(
                    cfg.linear_client_id, cfg.linear_client_secret, code,
                    cfg.redirect_uri, urllib_transport,
                )
                if cfg.linear_token_path:
                    save_tokens(cfg.linear_token_path, record)
            except Exception as exc:  # noqa: BLE001 - shown to the operator
                log.exception("oauth exchange failed")
                return self._html(502, "Exchange failed", str(exc))
            return self._html(200, "Hermes installed",
                              "App token stored. You can close this tab and delegate an issue.")

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path in ("/healthz", "/health"):
                return self._send(200, {"status": "ok"})
            if path == "/status":
                # Public by default, because a bridge is often health-checked from
                # outside. Full detail only to a caller holding STATUS_TOKEN.
                if status_authorized(self.headers):
                    return self._send(200, status_info())
                return self._send(200, {"status": "ok", "service": "linear-agent"})
            if path == "/oauth/callback":
                return self._oauth_callback()
            return self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path.rstrip("/") != "/webhook":
                return self._send(404, {"error": "not found"})

            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._send(413, {"error": "bad length"})

            body = self.rfile.read(length)
            sig = self.headers.get(HEADER)
            if not verify_signature(cfg.webhook_secret, body, sig):
                # No unauthenticated path may ever reach the agent.
                log.warning("rejected webhook: bad signature")
                return self._send(401, {"error": "invalid signature"})

            try:
                payload = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._send(400, {"error": "invalid json"})

            # Optional replay window (LINEAR_MAX_SKEW_SECONDS); off by default because
            # Linear retries at 1 min / 1 h / 6 h and those are legitimate.
            if cfg.max_skew_seconds > 0:
                try:
                    stamp = float(payload.get("webhookTimestamp") or 0) / 1000.0
                except (TypeError, ValueError):
                    stamp = 0.0
                if not stamp or abs(time.time() - stamp) > cfg.max_skew_seconds:
                    log.warning("rejected webhook: timestamp outside the replay window")
                    return self._send(401, {"error": "stale webhook"})

            headers = {k: v for k, v in self.headers.items()}
            self._send(200, {"ok": True})  # ack fast; Linear retries on 5xx
            threading.Thread(
                target=on_event, args=(payload, headers), daemon=True
            ).start()

        def log_message(self, fmt, *args):
            log.info("%s - %s", self.address_string(), fmt % args)

    return Handler


def serve(cfg, on_event):
    httpd = ThreadingHTTPServer((cfg.bind, cfg.port), make_handler(cfg, on_event))
    log.info("listening on %s:%s", cfg.bind, cfg.port)
    return httpd
