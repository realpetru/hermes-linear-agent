"""Entry point: python3 -m linear_hermes"""
from __future__ import annotations

import datetime
import logging
import os
import signal

from .adapters import build_adapter
from .config import ConfigError, load_config
from .dedupe import DeliveryStore
from .handler import AgentSessionHandler
from .linear import DeferredLinearClient, LinearClient, urllib_transport
from .oauth import TokenProvider, load_tokens
from .server import serve, serve_unconfigured
from . import runtime_status


def _serve_unconfigured(exc):
    """Stay up when the config is incomplete, so the container is inspectable.

    `python -m linear_hermes` is the image's CMD. Raising out of load_config made the
    container crash-loop with no way to see why; now /healthz answers 503 with the reason.
    """
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log = logging.getLogger("linear_hermes")
    missing = [name for name in ("LINEAR_WEBHOOK_SECRET",) if not (os.environ.get(name) or "").strip()]
    bind = os.environ.get("BIND") or "127.0.0.1"
    port = int(os.environ.get("PORT") or 8650)
    log.error("not configured: %s", exc)
    httpd = serve_unconfigured(bind, port, str(exc), missing)
    signal.signal(signal.SIGTERM, lambda *_: httpd.shutdown())
    httpd.serve_forever()
    return 0


def main():
    try:
        cfg = load_config()
    except ConfigError as exc:
        return _serve_unconfigured(exc)
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("linear_hermes")
    log.info("startup config: %s", cfg.redacted())

    store = DeliveryStore(cfg.db_path, max_concurrency=cfg.max_concurrency)
    # An agent must post as the app, so prefer the app token. The personal API key
    # is a dev fallback only: activities posted with it appear under the installer’s identity.
    tokens = load_tokens(cfg.linear_token_path) if cfg.linear_token_path else None
    if tokens and cfg.linear_client_id:
        provider = TokenProvider(
            cfg.linear_client_id, cfg.linear_client_secret, cfg.linear_token_path,
            transport=urllib_transport,
        )
        linear = LinearClient(provider.token)
        log.info("using Linear APP token (posts as the agent)")
    elif cfg.linear_api_key and not cfg.allow_personal_key:
        # The failure this project exists to prevent: comments appearing as a human. It is now
        # opt-in, loud, and off by default.
        linear = DeferredLinearClient(
            "LINEAR_API_KEY is set and deliberately ignored: a personal key posts activities "
            "under the installer's identity, not the agent's. Set LINEAR_ALLOW_PERSONAL_KEY=1 "
            "only to debug, or finish the install with `hermes linear-agent authorize`."
        )
        log.warning("LINEAR_API_KEY is set and IGNORED — set LINEAR_ALLOW_PERSONAL_KEY=1 to use "
                    "it for debugging (activities would appear under the installer's identity)")
    elif cfg.linear_api_key:
        linear = LinearClient(cfg.linear_api_key)
        log.warning("LINEAR_ALLOW_PERSONAL_KEY is set: activities will appear under the "
                    "installer's identity, not the agent's — debugging only")
    else:
        # A fresh install has no credential yet: that is normal, not fatal.
        linear = DeferredLinearClient(
            "no Linear credential yet. Finish the install: open the URL from "
            "`make authorize`, then the callback stores the app token. "
            "Webhooks received before that are acknowledged and logged, not run."
        )
        log.warning("no Linear credential yet — server starts, Linear calls will "
                    "fail with a clear message until you run `make authorize`")
    # One obvious setting, validated at boot with a human message rather than a trace.
    from .profiles import ProfileError, available_profiles, validate_profile
    from .routing import choose_profile

    try:
        validate_profile(cfg.agent_profile)
        for label, prof in (cfg.routing or {}).items():
            validate_profile(prof)
    except ProfileError as exc:
        log.error("%s", exc)
        raise SystemExit(2)
    log.info("agent profile: %s", cfg.agent_profile or "(gateway default)")
    if cfg.routing:
        log.info("label routing: %s", ", ".join("%s -> %s" % (k, v) for k, v in cfg.routing.items()))

    # Re-read settings per session (5s TTL) so switching agents needs no restart.
    from .live_config import LiveConfig
    from .profiles import available_profiles

    live = LiveConfig(load_config, ttl=cfg.refresh_seconds)

    # One label lookup per issue, not per event: replies in the same session reuse it.
    label_cache = {}

    def profile_for(payload):
        from .routing import resolve_labels

        current = live.get()
        labels = resolve_labels(payload, fetch=linear.issue_label_names, cache=label_cache)
        return choose_profile(
            payload, current.agent_profile, current.routing, available_profiles(), labels=labels
        )

    def adapter_for(payload):
        return build_adapter(live.get(), profile=profile_for(payload))

    handler = AgentSessionHandler(
        linear, build_adapter(cfg), store, cfg.agent_name,
        heartbeat_seconds=cfg.heartbeat_seconds, adapter_for=adapter_for,
        profile_for=profile_for,
    )

    def on_event(payload, headers):
        info = handler.handle(payload, headers)
        if not info.get("handled"):
            log.info("event ignored: %s", info.get("reason"))
            return
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        runtime_status.record_session(stamp)
        prompt = handler.build_prompt(payload)
        try:
            handler.run_session(info["session"], prompt, payload=payload)
        except Exception as exc:  # noqa: BLE001 - surfaced at /status
            runtime_status.record_failure("sessions: %s" % exc, stamp)
            raise

    httpd = serve(cfg, on_event)
    signal.signal(signal.SIGTERM, lambda *_: httpd.shutdown())
    httpd.serve_forever()


if __name__ == "__main__":
    main()
