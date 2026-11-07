"""Process-local counters surfaced at /status. No secrets, no Linear IDs."""
from __future__ import annotations

import threading

_lock = threading.Lock()
_state = {
    "sessions_handled": 0,
    "sessions_failed": 0,
    "last_error": "",
    "last_session_at": "",
}


def record_session(started_at):
    with _lock:
        _state["sessions_handled"] += 1
        _state["last_session_at"] = started_at


def record_failure(message, at):
    with _lock:
        _state["sessions_failed"] += 1
        _state["last_error"] = (message or "")[:400]
        _state["last_session_at"] = at


def snapshot():
    with _lock:
        return dict(_state)
