"""`python -m linear_hermes.exchange <code>` — turn a redirect ?code= into a token.

Lives inside the package (not only scripts/) so it is present in the container image.
Accepts either a bare code or a whole redirect URL pasted from the browser.
"""
from __future__ import annotations

import os
import sys
from urllib.parse import parse_qs, urlparse

from .linear import urllib_transport
from .oauth import exchange_code, save_tokens


def extract_code(text):
    text = (text or "").strip()
    if "code=" in text:
        return parse_qs(urlparse(text).query).get("code", [""])[0]
    return text


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python -m linear_hermes.exchange <code|redirect-url>")
        return 2
    code = extract_code(argv[0])
    if not code:
        print("no code found in the argument")
        return 2
    from .secrets import read_setting

    client_id = read_setting("LINEAR_CLIENT_ID", "") or ""
    client_secret = read_setting("LINEAR_CLIENT_SECRET", "") or ""
    public_url = (read_setting("LINEAR_AGENT_PUBLIC_URL", "") or "").rstrip("/")
    redirect_uri = read_setting("LINEAR_REDIRECT_URI", "") or (public_url + "/oauth/callback")
    if not redirect_uri:
        print(
            "set LINEAR_AGENT_PUBLIC_URL (or LINEAR_REDIRECT_URI) so the token exchange "
            "sends the same redirect_uri the authorize URL asked for"
        )
        return 2
    from .config import default_data_dir

    path = read_setting("LINEAR_TOKEN_PATH", "") or os.path.join(default_data_dir(), "linear-tokens.json")
    if not (client_id and client_secret):
        print("LINEAR_CLIENT_ID and LINEAR_CLIENT_SECRET must be set")
        print("  inside Hermes:  hermes config set LINEAR_CLIENT_ID <value>  (same for the secret)")
        print("  standalone:     export them, or name a file with LINEAR_AGENT_ENV_FILE")
        return 2
    try:
        record = exchange_code(client_id, client_secret, code, redirect_uri, urllib_transport)
    except Exception as exc:  # noqa: BLE001 - operator-facing command
        print("exchange failed: %s" % exc)
        return 1
    save_tokens(path, record)
    print("stored app token at %s (scope: %s)" % (path, record.get("scope") or "unknown"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
