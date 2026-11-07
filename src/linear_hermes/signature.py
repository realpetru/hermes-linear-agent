"""Linear webhook signature verification.

Linear sends `Linear-Signature`: the hex-encoded HMAC-SHA256 of the raw request
body, keyed with the webhook signing secret. Verify against the RAW bytes, never
against re-serialised JSON.
"""
from __future__ import annotations

import hashlib
import hmac

HEADER = "Linear-Signature"


def compute_signature(secret, body):
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if isinstance(body, str):
        body = body.encode("utf-8")
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def verify_signature(secret, body, header_value):
    """Constant-time compare. Tolerates an optional `sha256=` prefix and case."""
    if not secret or not header_value:
        return False
    provided = header_value.strip()
    if provided.lower().startswith("sha256="):
        provided = provided.split("=", 1)[1]
    provided = provided.strip().lower()
    expected = compute_signature(secret, body)
    return hmac.compare_digest(expected, provided)
