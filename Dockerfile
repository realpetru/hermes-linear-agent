FROM python:3.12-slim

WORKDIR /app
COPY src ./src
COPY scripts ./scripts
# No pip install: the plugin is stdlib-only and Hermes loads __init__.py directly.
# Shipping a [project] table made Hermes' plugin admission attempt an editable
# install into its managed venv, which fails on pre-existing installs.
ENV PYTHONPATH=/app/src

ENV PYTHONUNBUFFERED=1 \
    PORT=8650 \
    BIND=0.0.0.0 \
    STATE_DB=/data/state.sqlite3 \
    LINEAR_TOKEN_PATH=/data/linear-tokens.json

VOLUME ["/data"]
EXPOSE 8650
CMD ["python", "-m", "linear_hermes"]
