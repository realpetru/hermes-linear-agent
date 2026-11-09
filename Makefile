PY ?= python3
export PYTHONPATH := src

.PHONY: help dev test demo clean docker authorize exchange

help:
	@echo "make dev     - run the webhook service locally (credentials via Hermes' scope,"
	@echo "               the process environment, or the file named by LINEAR_AGENT_ENV_FILE)"
	@echo "make test    - run the unit tests (no network, no secrets)"
	@echo "make authorize - print the Linear OAuth URL to open as workspace admin"
	@echo "make exchange  - store the app token: make exchange CODE=<code>"
	@echo "make docker    - build and run with docker compose"
	@echo "hermes linear-agent doctor  - check every part of an install (recommended)"

dev:
	$(PY) -m linear_hermes

test:
	$(PY) -m unittest discover -s tests -v

# These helpers resolve values through the same read_setting path as the plugin:
# Hermes' profile secret scope inside Hermes, process environment (or the explicitly
# named LINEAR_AGENT_ENV_FILE) standalone. Neither one reads a .env implicitly.
authorize:
	@$(PY) -c "from linear_hermes.oauth import authorize_url, make_state; \
from linear_hermes.secrets import read_setting; \
from linear_hermes.settings import stored_state_key; \
client_id = read_setting('LINEAR_CLIENT_ID', ''); \
assert client_id, 'LINEAR_CLIENT_ID is not set — run: hermes config set LINEAR_CLIENT_ID <value>'; \
public = (read_setting('LINEAR_AGENT_PUBLIC_URL', '') or '').rstrip('/'); \
redirect = read_setting('LINEAR_REDIRECT_URI', '') or (public + '/oauth/callback' if public else ''); \
assert redirect, 'set LINEAR_AGENT_PUBLIC_URL (or LINEAR_REDIRECT_URI)'; \
key = read_setting('LINEAR_STATE_KEY', '') or stored_state_key() or read_setting('LINEAR_WEBHOOK_SECRET', ''); \
assert key, 'no install-link key — run: hermes linear-agent setup'; \
print(authorize_url(client_id, redirect, state=make_state(key)))"

exchange:
	@test -n "$(CODE)" || (echo "usage: make exchange CODE=<code from redirect>"; exit 2)
	@$(PY) -m linear_hermes.exchange "$(CODE)"

docker:
	docker compose up --build

clean:
	find . -name __pycache__ -type d -exec rm -rf {} +
