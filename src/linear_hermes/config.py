"""Environment-driven configuration. Secrets are never logged or echoed."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)


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
    allow_personal_key: bool = False
    linear_client_id: str = ""
    linear_client_secret: str = ""
    linear_token_path: str = ""
    redirect_uri: str = ""
    public_url: str = ""
    heartbeat_seconds: int = 45
    max_skew_seconds: int = 0
    agent_profile: str = ""
    routing: dict = None
    refresh_seconds: float = 5.0
    state_key: str = ""            # signs the OAuth install link, never Linear's webhook secret

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
            "public_url": self.public_url or "MISSING",
            "redirect_uri": self.redirect_uri or "MISSING",
            "linear_token_path": self.linear_token_path,
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


def credentials_file():
    """THE place credentials live. One store, chosen the same way every time.

    ``setup`` writes here and ``read_setting`` reads here, so a fresh process can never
    report "credentials missing" while they sit in a file next door — which is exactly what
    happened when setup wrote ``./.env`` in the caller's directory while doctor, status and
    authorize read the Hermes secret scope and ``os.environ``.

    Inside Hermes that is the profile's own ``.env`` (``$HERMES_HOME/.env``), the file the
    gateway loads for this profile. Standalone (``make dev`` in a checkout) it stays ``.env``
    next to the code, where nothing but a developer looks.
    """
    explicit = os.environ.get("LINEAR_AGENT_ENV_FILE")
    if explicit:
        return os.path.abspath(explicit)
    if os.environ.get("HERMES_HOME"):
        from .settings import hermes_home

        return os.path.join(hermes_home(), ".env")
    try:
        import agent.secret_scope  # noqa: F401  - present only inside Hermes

        from .settings import hermes_home

        return os.path.join(hermes_home(), ".env")
    except Exception:  # noqa: BLE001 - standalone checkout, no Hermes in sight
        return ".env"


def default_env_file():
    """Backwards-compatible name for the same file; keep one implementation."""
    return credentials_file()


def _effective_profile(env):
    """env LINEAR_AGENT_PROFILE beats the persisted plugin setting."""
    value = _env(env, "LINEAR_AGENT_PROFILE", "")
    if value:
        return str(value).strip().lower()
    from .settings import stored_profile

    return stored_profile()


def default_data_dir():
    """Where the token and the dedupe database live unless told otherwise.

    Deliberately absolute: a relative path is resolved against the process's working
    directory, so a service started elsewhere loses the app token and silently falls
    back to posting as a human — the exact bug this project exists to fix.
    """
    from .settings import settings_dir

    return settings_dir()


def _absolute_data_path(value, name):
    if not value:
        return value
    expanded = os.path.expanduser(str(value))
    if os.path.isabs(expanded):
        return expanded
    resolved = os.path.abspath(expanded)
    log.warning(
        "%s is relative (%s); using %s. Set an absolute path so a service started "
        "from another directory can still find it.",
        name,
        value,
        resolved,
    )
    return resolved


def _effective_routing(env):
    raw = _env(env, "LINEAR_AGENT_ROUTING", "")
    if raw:
        return _parse_routing(raw)
    from .settings import read_settings

    stored = read_settings().get("routing")
    return {str(k).lower(): str(v).strip().lower() for k, v in stored.items()} if isinstance(stored, dict) else {}


def load_config(env=None):
    if env is None:
        env = os.environ
        dotenv = load_dotenv(default_env_file())
        if dotenv:
            merged = dict(dotenv)
            merged.update({k: v for k, v in os.environ.items() if k in dotenv})
            env = merged
    public_url = (_env(env, "LINEAR_AGENT_PUBLIC_URL", "") or "").rstrip("/")
    # A browser-facing callback: an explicit value, else the public host, and NO
    # fall-back to the internal bridge URL (Linear has never heard of that address).
    redirect = _env(env, "LINEAR_REDIRECT_URI", "") or (
        (public_url + "/oauth/callback") if public_url else ""
    )
    token_path = _absolute_data_path(
        _env(env, "LINEAR_TOKEN_PATH", "") or os.path.join(default_data_dir(), "linear-tokens.json"),
        "LINEAR_TOKEN_PATH",
    )
    state_db = _absolute_data_path(
        _env(env, "STATE_DB", "") or os.path.join(default_data_dir(), "state.sqlite3"),
        "STATE_DB",
    )
    cfg = Config(
        webhook_secret=_env(env, "LINEAR_WEBHOOK_SECRET", "") or "",
        state_key=_env(env, "LINEAR_STATE_KEY", "") or "",
        linear_api_key=_env(env, "LINEAR_API_KEY", "") or "",
        allow_personal_key=str(_env(env, "LINEAR_ALLOW_PERSONAL_KEY", "") or "").strip().lower()
        in ("1", "true", "yes", "on"),
        hermes_adapter=(_env(env, "HERMES_ADAPTER", "gateway") or "gateway").lower(),
        gateway_url=(_env(env, "HERMES_GATEWAY_URL", "http://127.0.0.1:8642") or "").rstrip("/"),
        gateway_token=_env(env, "HERMES_GATEWAY_TOKEN", "") or "",
        hermes_bin=_env(env, "HERMES_BIN", "hermes") or "hermes",
        port=int(_env(env, "PORT", "8650")),
        bind=_env(env, "BIND", "127.0.0.1") or "127.0.0.1",
        max_concurrency=int(_env(env, "MAX_CONCURRENCY", "3")),
        db_path=state_db,
        agent_name=_env(env, "AGENT_DISPLAY_NAME", "Hermes") or "Hermes",
        log_level=(_env(env, "LOG_LEVEL", "INFO") or "INFO").upper(),
        linear_client_id=_env(env, "LINEAR_CLIENT_ID", "") or "",
        linear_client_secret=_env(env, "LINEAR_CLIENT_SECRET", "") or "",
        linear_token_path=token_path,
        redirect_uri=redirect,
        public_url=public_url,
        heartbeat_seconds=int(_env(env, "HEARTBEAT_SECONDS", "45")),
        max_skew_seconds=int(_env(env, "LINEAR_MAX_SKEW_SECONDS", "0")),
        agent_profile=_effective_profile(env),
        routing=_effective_routing(env),
        refresh_seconds=float(_env(env, "LINEAR_AGENT_REFRESH_SECONDS", "5") or 5),
    )
    if not cfg.webhook_secret:
        raise ConfigError("LINEAR_WEBHOOK_SECRET is not set")
    if cfg.hermes_adapter not in ("gateway", "subprocess"):
        raise ConfigError("HERMES_ADAPTER must be 'gateway' or 'subprocess'")
    if cfg.hermes_adapter == "gateway" and not cfg.gateway_url:
        raise ConfigError("HERMES_GATEWAY_URL is required for the gateway adapter")
    return cfg
