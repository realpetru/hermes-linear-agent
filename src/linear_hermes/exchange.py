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


def _env_with_dotenv():
    """Read the repo .env the docs tell people to fill in, then the real environment.

    `make exchange` exports nothing, so without this the documented fallback answers
    "LINEAR_CLIENT_ID and LINEAR_CLIENT_SECRET must be set" seconds after the user
    wrote them into the file the guide named.
    """
    from .config import default_env_file, load_dotenv

    values = dict(load_dotenv(default_env_file()))
    values.update(os.environ)
    return values


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python -m linear_hermes.exchange <code|redirect-url>")
        return 2
    code = extract_code(argv[0])
    if not code:
        print("no code found in the argument")
        return 2
    env = _env_with_dotenv()
    client_id = env.get("LINEAR_CLIENT_ID", "")
    client_secret = env.get("LINEAR_CLIENT_SECRET", "")
    public_url = (env.get("LINEAR_AGENT_PUBLIC_URL", "") or "").rstrip("/")
    redirect_uri = env.get("LINEAR_REDIRECT_URI", "") or (public_url + "/oauth/callback")
    if not redirect_uri:
        print(
            "set LINEAR_AGENT_PUBLIC_URL (or LINEAR_REDIRECT_URI) so the token exchange "
            "sends the same redirect_uri the authorize URL asked for"
        )
        return 2
    from .config import default_data_dir

    path = env.get("LINEAR_TOKEN_PATH", "") or os.path.join(default_data_dir(), "linear-tokens.json")
    if not (client_id and client_secret):
        print("LINEAR_CLIENT_ID and LINEAR_CLIENT_SECRET must be set")
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
