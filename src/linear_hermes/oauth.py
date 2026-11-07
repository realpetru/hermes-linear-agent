"""Linear OAuth for an agent app (actor=app).

An agent must post activities as *itself*, so it needs the app's own access token,
not a personal API key. Flow: open the authorize URL as a workspace admin, Linear
redirects to the callback with ?code=..., exchange it for access + refresh tokens,
store them 0600, refresh before expiry.

Scopes required for an agent that can be assigned and mentioned:
    read,write,app:assignable,app:mentionable
`actor=app` is what makes the install an app installation rather than a user login.
"""
from __future__ import annotations

import json
import os
import stat
import time
import urllib.parse

AUTHORIZE_URL = "https://linear.app/oauth/authorize"
TOKEN_URL = "https://api.linear.app/oauth/token"
REQUIRED_SCOPES = ["read", "write", "app:assignable", "app:mentionable"]


class OAuthError(RuntimeError):
    pass


def authorize_url(client_id, redirect_uri, scopes=None, state=""):
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": ",".join(scopes or REQUIRED_SCOPES),
        "actor": "app",
    }
    if state:
        params["state"] = state
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


def parse_token_response(payload, now=None):
    """Normalise Linear's token response into a storable record."""
    if "access_token" not in payload:
        raise OAuthError("token response has no access_token: %s" % sorted(payload))
    now = time.time() if now is None else now
    expires_in = int(payload.get("expires_in") or 0)
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", ""),
        "token_type": payload.get("token_type", "Bearer"),
        "scope": payload.get("scope", ""),
        "obtained_at": now,
        "expires_at": (now + expires_in) if expires_in else None,
    }


def needs_refresh(record, now=None, skew=300):
    """Refresh 5 minutes early; no expiry recorded means do not refresh blindly."""
    if not record.get("expires_at"):
        return False
    now = time.time() if now is None else now
    return now >= (record["expires_at"] - skew)


def save_tokens(path, record):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(record, fh)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    os.replace(tmp, path)


def load_tokens(path):
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def exchange_code(client_id, client_secret, code, redirect_uri, transport, url=TOKEN_URL):
    form = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    status, payload = transport(url, {"Content-Type": "application/x-www-form-urlencoded"}, form, 30)
    if status != 200:
        raise OAuthError("token exchange HTTP %s" % status)
    return parse_token_response(payload)


def refresh(client_id, client_secret, refresh_token, transport, url=TOKEN_URL):
    form = urllib.parse.urlencode({
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
    }).encode()
    status, payload = transport(url, {"Content-Type": "application/x-www-form-urlencoded"}, form, 30)
    if status != 200:
        raise OAuthError("token refresh HTTP %s" % status)
    return parse_token_response(payload)


class TokenProvider:
    """Hands LinearClient a live access token, refreshing on demand."""

    def __init__(self, client_id="", client_secret="", path="", transport=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.path = path
        self.transport = transport
        self._record = load_tokens(path) if path else None

    def token(self):
        if not self._record:
            raise OAuthError("no stored Linear app token; run the install flow")
        if needs_refresh(self._record) and self._record.get("refresh_token"):
            self._record = refresh(
                self.client_id, self.client_secret, self._record["refresh_token"], self.transport
            )
            if self.path:
                save_tokens(self.path, self._record)
        return self._record["access_token"]
