"""Environment-driven configuration. Secrets are never logged or echoed."""
from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


def _env(name, default=None):
    v = os.environ.get(name)
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
            "linear_client_id": "set" if self.linear_client_id else "MISSING",
            "linear_client_secret": "set" if self.linear_client_secret else "MISSING",
        }


def load_config(env=None):
    env = os.environ if env is None else env
    cfg = Config(
        webhook_secret=_env("LINEAR_WEBHOOK_SECRET", "") or "",
        linear_api_key=_env("LINEAR_API_KEY", "") or "",
        hermes_adapter=(_env("HERMES_ADAPTER", "gateway") or "gateway").lower(),
        gateway_url=(_env("HERMES_GATEWAY_URL", "http://127.0.0.1:8642") or "").rstrip("/"),
        gateway_token=_env("HERMES_GATEWAY_TOKEN", "") or "",
        hermes_bin=_env("HERMES_BIN", "hermes") or "hermes",
        port=int(_env("PORT", "8650")),
        bind=_env("BIND", "127.0.0.1") or "127.0.0.1",
        max_concurrency=int(_env("MAX_CONCURRENCY", "3")),
        db_path=_env("STATE_DB", "/tmp/hermes-linear-agent/state.sqlite3") or "/tmp/hermes-linear-agent/state.sqlite3",
        agent_name=_env("AGENT_DISPLAY_NAME", "Hermes") or "Hermes",
        log_level=(_env("LOG_LEVEL", "INFO") or "INFO").upper(),
        linear_client_id=_env("LINEAR_CLIENT_ID", "") or "",
        linear_client_secret=_env("LINEAR_CLIENT_SECRET", "") or "",
        linear_token_path=_env("LINEAR_TOKEN_PATH", "data/linear-tokens.json") or "",
        redirect_uri=_env("LINEAR_REDIRECT_URI", "http://localhost:8650/oauth/callback") or "",
        heartbeat_seconds=int(_env("HEARTBEAT_SECONDS", "45")),
        max_skew_seconds=int(_env("LINEAR_MAX_SKEW_SECONDS", "0")),
    )
    if not cfg.webhook_secret:
        raise ConfigError("LINEAR_WEBHOOK_SECRET is not set")
    if cfg.hermes_adapter not in ("gateway", "subprocess"):
        raise ConfigError("HERMES_ADAPTER must be 'gateway' or 'subprocess'")
    if cfg.hermes_adapter == "gateway" and not cfg.gateway_url:
        raise ConfigError("HERMES_GATEWAY_URL is required for the gateway adapter")
    return cfg
