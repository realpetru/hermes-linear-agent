"""Entry point: python3 -m linear_hermes"""
from __future__ import annotations

import datetime
import logging
import signal

from .adapters import build_adapter
from .config import load_config
from .dedupe import DeliveryStore
from .handler import AgentSessionHandler
from .linear import DeferredLinearClient, LinearClient, urllib_transport
from .oauth import TokenProvider, load_tokens
from .server import serve
from . import runtime_status


def main():
    cfg = load_config()
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
    elif cfg.linear_api_key:
        linear = LinearClient(cfg.linear_api_key)
        log.warning("no Linear app token; falling back to personal API key "
                    "(activities will appear under the installer's identity)")
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

    def adapter_for(payload):
        return build_adapter(cfg, profile=choose_profile(payload, cfg.agent_profile, cfg.routing))

    handler = AgentSessionHandler(
        linear, build_adapter(cfg), store, cfg.agent_name,
        heartbeat_seconds=cfg.heartbeat_seconds, adapter_for=adapter_for,
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
