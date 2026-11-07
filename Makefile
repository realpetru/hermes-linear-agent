PY ?= python3
export PYTHONPATH := src

.PHONY: help dev test demo clean docker authorize exchange

help:
	@echo "make dev     - run the webhook service locally (needs .env)"
	@echo "make test    - run the unit tests (no network, no secrets)"
	@echo "make authorize - print the Linear OAuth URL to open as workspace admin"
	@echo "make exchange  - store the app token: make exchange CODE=<code>"
	@echo "make docker    - build and run with docker compose"

dev:
	$(PY) -m linear_hermes

test:
	$(PY) -m unittest discover -s tests -v

authorize:
	@$(PY) -c "import os;from linear_hermes.oauth import authorize_url;\
	print(authorize_url(os.environ.get('LINEAR_CLIENT_ID','FILL_ME'),\
	os.environ.get('LINEAR_REDIRECT_URI','http://localhost:8650/oauth/callback')))"

exchange:
	@test -n "$(CODE)" || (echo "usage: make exchange CODE=<code from redirect>"; exit 2)
	PYTHONPATH=src $(PY) scripts/oauth_exchange.py "$(CODE)"

docker:
	docker compose up --build

clean:
	find . -name __pycache__ -type d -exec rm -rf {} +
