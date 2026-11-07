"""One place to swap how Hermes is invoked.

Contract supplied by @hermes from the api_server source (port 8642):
  POST /v1/runs  auth: Authorization: Bearer $API_SERVER_KEY
  body: {"input": ...}  (legacy key "message"; optional session_id/model/previous_response_id)
  -> returns a run id immediately; GET /v1/runs/{id} for status, /events to stream.
  Idempotency-Key header is supported server-side.
  In-flight statuses: queued, running, waiting_for_approval, stopping.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request

IN_FLIGHT = {"queued", "running", "waiting_for_approval", "stopping"}
OUTPUT_KEYS = ("output", "response", "result", "text", "message", "content")


class AdapterError(RuntimeError):
    pass


class GatewayAdapter:
    """Preferred transport: the Hermes API server. Never expose it unauthenticated."""

    def __init__(self, base_url, api_key="", timeout=900, poll_interval=1.5):
        if not base_url:
            raise AdapterError("HERMES_GATEWAY_URL is required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.poll_interval = poll_interval

    def _request(self, path, payload=None, idempotency_key=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self.base_url + path, data=data, method="POST" if data else "GET"
        )
        if data:
            req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", "Bearer " + self.api_key)
        if idempotency_key:
            req.add_header("Idempotency-Key", idempotency_key)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:300]
            except Exception:
                pass
            raise AdapterError("gateway %s -> HTTP %s %s" % (path, exc.code, detail))

    def run(self, prompt, progress=None, idempotency_key=None):
        created = self._request(
            "/v1/runs", {"input": prompt}, idempotency_key=idempotency_key
        )
        run_id = created.get("run_id") or created.get("id")
        if not run_id:
            raise AdapterError("gateway returned no run id (keys: %s)" % sorted(created))
        deadline = time.time() + self.timeout
        last = ""
        while time.time() < deadline:
            state = self._request("/v1/runs/" + str(run_id))
            status = str(state.get("status") or "").lower()
            if status and status != last:
                last = status
                if progress:
                    progress("status: %s" % status)
            if status and status not in IN_FLIGHT:
                if status in ("failed", "error", "cancelled", "stopped"):
                    raise AdapterError("run %s ended %s" % (run_id, status))
                return self._extract_output(state)
            time.sleep(self.poll_interval)
        raise AdapterError("run %s timed out after %ss" % (run_id, self.timeout))

    @staticmethod
    def _extract_output(state):
        for key in OUTPUT_KEYS:
            value = state.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""


class SubprocessAdapter:
    """Fallback: `hermes chat -q`, the mechanism phase 1 proved."""

    def __init__(self, hermes_bin="hermes", profile=None, timeout=900):
        self.hermes_bin = hermes_bin
        self.profile = profile
        self.timeout = timeout

    def run(self, prompt, progress=None, idempotency_key=None):
        cmd = [self.hermes_bin, "chat", "-q", prompt]
        if self.profile:
            cmd = [self.hermes_bin, "-p", self.profile, "chat", "-q", prompt]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise AdapterError("hermes chat timed out after %ss" % self.timeout)
        if proc.returncode != 0:
            raise AdapterError(
                "hermes chat exited %s: %s" % (proc.returncode, (proc.stderr or "")[-400:])
            )
        return (proc.stdout or "").strip()


def build_adapter(cfg):
    if cfg.hermes_adapter == "gateway":
        return GatewayAdapter(cfg.gateway_url, cfg.gateway_token)
    return SubprocessAdapter(cfg.hermes_bin)
