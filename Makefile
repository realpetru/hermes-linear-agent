PY ?= python3
export PYTHONPATH := src

.PHONY: help dev test demo clean docker authorize exchange

help:
	@echo "make dev     - run the webhook service locally (needs .env)"
	@echo "make test    - run the unit tests (no network, no secrets)"
	@echo "make authorize - print the Linear OAuth URL to open as workspace admin"
	@echo "make exchange  - store the app token: make exchange CODE=<code>"
	@echo "make docker    - build and run with docker compose"
	@echo "hermes linear-agent doctor  - check every part of an install (recommended)"

dev:
	$(PY) -m linear_hermes

test:
	$(PY) -m unittest discover -s tests -v

# The helper targets below load the credentials file themselves; `hermes linear-agent ...`
# does not need this because it reads the same file through read_setting.
authorize:
	@set -a; [ -f "$${LINEAR_AGENT_ENV_FILE:-.env}" ] && . "$${LINEAR_AGENT_ENV_FILE:-.env}"; set +a; \
	PYTHONPATH=src $(PY) -c "import os; from linear_hermes.oauth import authorize_url, make_state; \
	key = os.environ.get('LINEAR_STATE_KEY') or os.environ.get('LINEAR_WEBHOOK_SECRET'); \
	assert key, 'LINEAR_STATE_KEY is not set — run: hermes linear-agent setup'; \
	print(authorize_url(os.environ['LINEAR_CLIENT_ID'], \
	os.environ.get('LINEAR_REDIRECT_URI') or (os.environ.get('LINEAR_AGENT_PUBLIC_URL','').rstrip('/') + '/oauth/callback'), \
	state=make_state(key)))"

exchange:
	@test -n "$(CODE)" || (echo "usage: make exchange CODE=<code from redirect>"; exit 2)
	@set -a; [ -f .env ] && . ./.env; set +a; PYTHONPATH=src $(PY) scripts/oauth_exchange.py "$(CODE)"

docker:
	docker compose up --build

clean:
	find . -name __pycache__ -type d -exec rm -rf {} +
