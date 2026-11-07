"""Turn a verified Linear agent-session event into a Hermes run.

Linear marks a session unresponsive if no activity arrives within 10 seconds of a
`created` event, so the `thought` is emitted before any context fetch or model work.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

ACK = "Picked this up — reading the issue now."


class AgentSessionHandler:
    """Collaborators are injected so the whole flow is testable without network.

    linear  - LinearClient-like: post_activity(session_id, kind, body=..., **fields)
    adapter - GatewayAdapter/SubprocessAdapter-like: run(prompt, progress, idempotency_key)
    store   - DeliveryStore
    """

    def __init__(self, linear, adapter, store, agent_name="Hermes", heartbeat_seconds=45,
                 adapter_for=None):
        self.linear = linear
        self.adapter = adapter
        # adapter_for(payload) lets one bridge answer different issues as different
        # Hermes agents (label routing). Defaults to the single adapter.
        self.adapter_for = adapter_for
        self.store = store
        self.agent_name = agent_name
        self.heartbeat_seconds = heartbeat_seconds

    # -- event parsing ---------------------------------------------------
    @staticmethod
    def _session_id(payload):
        session = payload.get("agentSession") or {}
        return session.get("id") or payload.get("agentSessionId")

    @staticmethod
    def _delivery_id(payload, headers):
        for h in ("Linear-Delivery", "X-Linear-Delivery", "Linear-Webhook-Id"):
            if headers.get(h):
                return headers[h]
        return payload.get("webhookId") or payload.get("id")

    @staticmethod
    def should_handle(payload):
        """Only AgentSessionEvent. Actions are `created` and `prompted`."""
        if str(payload.get("type") or "").lower() not in ("agentsessionevent", ""):
            return False
        action = str(payload.get("action") or "").lower()
        return action in ("created", "prompted")

    # -- prompt ----------------------------------------------------------
    def build_prompt(self, payload):
        """Prefer Linear's own promptContext; fall back to the structured issue."""
        ctx = payload.get("promptContext")
        if isinstance(ctx, str) and ctx.strip():
            return ctx.strip()
        from . import prompt as prompt_mod

        issue = (payload.get("agentSession") or {}).get("issue") or payload.get("issue") or {}
        if issue.get("id"):
            try:
                fetched = self.linear.issue_context(issue["id"])
                if fetched:
                    issue = fetched
            except Exception:
                log.warning("issue context fetch failed; using payload copy")
        return prompt_mod.render(issue)

    # -- orchestration ---------------------------------------------------
    def handle(self, payload, headers=None):
        headers = headers or {}
        if not self.should_handle(payload):
            return {"handled": False, "reason": "not_agent_session"}

        session = self._session_id(payload)
        if not session:
            return {"handled": False, "reason": "no_session_id"}

        delivery = self._delivery_id(payload, headers)
        # Dedupe on (session, delivery): a Linear RETRY reuses the delivery id and is
        # dropped, while a follow-up prompt in the same session has a new delivery id
        # and must run. Keying on the session alone silently killed every follow-up.
        replay_key = "replay:%s:%s" % (session, delivery or "none")
        if delivery and not self.store.claim(replay_key):
            log.info("duplicate delivery %s for session %s", delivery, session)
            return {"handled": False, "reason": "duplicate_delivery"}

        if not self.store.try_acquire_slot(session):
            # Refusing for capacity must not consume the delivery, or the retry the
            # message asks for would be rejected as a duplicate.
            self.store.unclaim(replay_key)
            self.linear.post_activity(
                session, "response",
                "I'm at capacity right now — reply again in a few minutes and I'll pick it up.",
            )
            return {"handled": False, "reason": "at_capacity"}

        # Acknowledge inside Linear's 10-second window, before any slow work.
        try:
            self.linear.post_activity(session, "thought", ACK)
        except Exception:
            log.exception("acknowledgement failed for session %s", session)
        return {"handled": True, "session": session, "delivery": delivery}

    def _start_heartbeat(self, session):
        """One 'still working' thought if a run outlives the threshold.

        Linear renders thoughts inline, so a long run stops looking dead. The
        activity is marked ephemeral: it is progress, not a result.
        """
        if not self.heartbeat_seconds or self.heartbeat_seconds <= 0:
            return None
        stop = threading.Event()

        def beat():
            if stop.wait(self.heartbeat_seconds):
                return
            try:
                self.linear.post_activity(
                    session, "thought",
                    "Still working on this — reading the code, not stuck.",
                    ephemeral=True,
                )
            except Exception:
                log.debug("heartbeat thought failed", exc_info=True)

        t = threading.Thread(target=beat, daemon=True)
        t.start()
        return stop

    def run_session(self, session, prompt, idempotency_key=None, payload=None):
        adapter = self.adapter
        if self.adapter_for is not None and payload is not None:
            adapter = self.adapter_for(payload)
        stop = self._start_heartbeat(session)
        try:
            text = adapter.run(
                prompt,
                progress=lambda msg: log.info("progress: %s", msg),
                idempotency_key=idempotency_key,
            )
            self.linear.post_activity(session, "response", text or "(no output)")
            return text
        except Exception as exc:  # noqa: BLE001 - the thread must always learn
            log.exception("agent session %s failed", session)
            try:
                self.linear.post_activity(session, "error", "Failed: %s" % exc)
            except Exception:
                log.exception("could not report error to Linear")
            raise
        finally:
            if stop is not None:
                stop.set()
            self.store.release_slot(session)
