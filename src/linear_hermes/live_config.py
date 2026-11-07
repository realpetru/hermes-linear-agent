"""Re-read configuration per session so switching agents needs no restart.

A webhook is rare (seconds apart at most), and the read is one small JSON file plus
the environment, so a short TTL is plenty and keeps an event loop cheap.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 5.0


class LiveConfig:
    """TTL-cached config. ``get()`` never raises: a bad read keeps the last good one."""

    def __init__(self, loader, ttl=DEFAULT_TTL_SECONDS, clock=time.monotonic):
        self._loader = loader
        self._ttl = max(0.0, float(ttl))
        self._clock = clock
        self._value = None
        self._loaded_at = None

    def get(self, force=False):
        now = self._clock()
        fresh = (
            self._value is not None
            and self._loaded_at is not None
            and (now - self._loaded_at) < self._ttl
        )
        if fresh and not force:
            return self._value
        try:
            value = self._loader()
        except Exception:  # noqa: BLE001 - never break a delivery over a config read
            if self._value is not None:
                log.warning("config reload failed; keeping the previous settings", exc_info=True)
                return self._value
            raise
        previous = getattr(self._value, "agent_profile", None)
        self._value = value
        self._loaded_at = now
        if previous is not None and previous != getattr(value, "agent_profile", None):
            log.info("agent profile: %s -> %s",
                     previous or "default", getattr(value, "agent_profile", "") or "default")
        return value
