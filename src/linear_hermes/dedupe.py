"""Idempotency and concurrency control.

Linear retries deliveries. One @mention must never spawn three Hermes runs, so we
claim two keys before doing work: the delivery id and the agent session id.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    key        TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS active (
    session_id TEXT PRIMARY KEY,
    started_at REAL NOT NULL
);
"""


class DeliveryStore:
    def __init__(self, path, max_concurrency=3):
        self.path = path
        self.max_concurrency = max_concurrency
        self._lock = threading.Lock()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # -- idempotency -----------------------------------------------------
    def claim(self, key, kind="delivery"):
        """True the first time a key is seen, False on any repeat."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO claims (key, kind, created_at) VALUES (?, ?, ?)",
                    (key, kind, time.time()),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    # -- concurrency -----------------------------------------------------
    def try_acquire_slot(self, session_id, stale_after=7200):
        """Reserve a slot. Stale slots (crashed worker) are swept first."""
        with self._lock:
            cutoff = time.time() - stale_after
            self._conn.execute("DELETE FROM active WHERE started_at < ?", (cutoff,))
            n = self._conn.execute("SELECT COUNT(*) FROM active").fetchone()[0]
            if n >= self.max_concurrency:
                return False
            self._conn.execute(
                "INSERT OR REPLACE INTO active (session_id, started_at) VALUES (?, ?)",
                (session_id, time.time()),
            )
            self._conn.commit()
            return True

    def release_slot(self, session_id):
        with self._lock:
            self._conn.execute("DELETE FROM active WHERE session_id = ?", (session_id,))
            self._conn.commit()

    def active_count(self):
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM active").fetchone()[0]
