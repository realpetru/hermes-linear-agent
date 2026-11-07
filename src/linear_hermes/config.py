"""Environment-driven configuration. Secrets are never logged or echoed."""
from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


def _env(env, name, default=None):
    """Read from the caller's mapping first, then Hermes' secret scope, then the env."""
    from .secrets import read_setting

    v = read_setting(name, None, env)
    if v is None:
        return default
    v = v.strip()
    return v or default


@dataclass(frozen=True)
class Config:
    webhook_secret: str
    linear_api_key: str
    hermes_adapter: str          # "gateway" | "subprocess"
    gateway_url: str
    gateway_token: str
    hermes_bin: str
    port: int
    bind: str
    max_concurrency: int
    db_path: str
    agent_name: str
    log_level: str
    linear_client_id: str = ""
    linear_client_secret: str = ""
    linear_token_path: str = ""
    redirect_uri: str = ""
    heartbeat_seconds: int = 45
    max_skew_seconds: int = 0
    agent_profile: str = ""
    routing: dict = None

    def redacted(self):
        """Safe-to-log view: proves a secret is present without revealing it."""
        return {
            "webhook_secret": "set" if self.webhook_secret else "MISSING",
            "linear_api_key": "set" if self.linear_api_key else "MISSING",
            "gateway_token": "set" if self.gateway_token else "MISSING",
            "hermes_adapter": self.hermes_adapter,
            "gateway_url": self.gateway_url,
            "port": self.port,
            "bind": self.bind,
            "max_concurrency": self.max_concurrency,
            "db_path": self.db_path,
            "agent_name": self.agent_name,
            "agent_profile": self.agent_profile or "(gateway default)",
            "routing": self.routing or {},
            "linear_client_id": "set" if self.linear_client_id else "MISSING",
            "linear_client_secret": "set" if self.linear_client_secret else "MISSING",
        }


def _parse_routing(raw):
    """LINEAR_AGENT_ROUTING is JSON: {"label": "profile", ...}."""
    if not raw:
        return {}
    import json as _json

    try:
        parsed = _json.loads(raw)
    except ValueError as exc:
        raise ConfigError("LINEAR_AGENT_ROUTING must be JSON like {\"cody\": \"cody\"}: %s" % exc)
    if not isinstance(parsed, dict):
        raise ConfigError("LINEAR_AGENT_ROUTING must be a JSON object of label -> profile")
    return {str(k).lower(): str(v).strip().lower() for k, v in parsed.items() if v}


def load_dotenv(path):
    """Minimal .env reader so `make dev` works exactly as INSTALL.md documents.

    Returns a dict; does not mutate os.environ. Lines are KEY=VALUE, `#` comments,
    optional surrounding quotes. Values are never logged.
    """
    values = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return values


def default_env_file():
    return os.environ.get("LINEAR_AGENT_ENV_FILE", ".env")


def load_config(env=None):
    if env is None:
        env = os.environ
        dotenv = load_dotenv(default_env_file())
        if dotenv:
            merged = dict(dotenv)
            merged.update({k: v for k, v in os.environ.items() if k in dotenv})
            env = merged
    cfg = Config(
        webhook_secret=_env(env, "LINEAR_WEBHOOK_SECRET", "") or "",
        linear_api_key=_env(env, "LINEAR_API_KEY", "") or "",
        hermes_adapter=(_env(env, "HERMES_ADAPTER", "gateway") or "gateway").lower(),
        gateway_url=(_env(env, "HERMES_GATEWAY_URL", "http://127.0.0.1:8642") or "").rstrip("/"),
        gateway_token=_env(env, "HERMES_GATEWAY_TOKEN", "") or "",
        hermes_bin=_env(env, "HERMES_BIN", "hermes") or "hermes",
        port=int(_env(env, "PORT", "8650")),
        bind=_env(env, "BIND", "127.0.0.1") or "127.0.0.1",
        max_concurrency=int(_env(env, "MAX_CONCURRENCY", "3")),
        db_path=_env(env, "STATE_DB", "/tmp/hermes-linear-agent/state.sqlite3") or "/tmp/hermes-linear-agent/state.sqlite3",
        agent_name=_env(env, "AGENT_DISPLAY_NAME", "Hermes") or "Hermes",
        log_level=(_env(env, "LOG_LEVEL", "INFO") or "INFO").upper(),
        linear_client_id=_env(env, "LINEAR_CLIENT_ID", "") or "",
        linear_client_secret=_env(env, "LINEAR_CLIENT_SECRET", "") or "",
        linear_token_path=_env(env, "LINEAR_TOKEN_PATH", "data/linear-tokens.json") or "",
        redirect_uri=_env(env, "LINEAR_REDIRECT_URI", "http://localhost:8650/oauth/callback") or "",
        heartbeat_seconds=int(_env(env, "HEARTBEAT_SECONDS", "45")),
        max_skew_seconds=int(_env(env, "LINEAR_MAX_SKEW_SECONDS", "0")),
        agent_profile=(_env(env, "LINEAR_AGENT_PROFILE", "") or "").strip().lower(),
        routing=_parse_routing(_env(env, "LINEAR_AGENT_ROUTING", "")),
    )
    if not cfg.webhook_secret:
        raise ConfigError("LINEAR_WEBHOOK_SECRET is not set")
    if cfg.hermes_adapter not in ("gateway", "subprocess"):
        raise ConfigError("HERMES_ADAPTER must be 'gateway' or 'subprocess'")
    if cfg.hermes_adapter == "gateway" and not cfg.gateway_url:
        raise ConfigError("HERMES_GATEWAY_URL is required for the gateway adapter")
    return cfg
