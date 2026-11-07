FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY scripts ./scripts
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1 \
    PORT=8650 \
    BIND=0.0.0.0 \
    STATE_DB=/data/state.sqlite3

VOLUME ["/data"]
EXPOSE 8650
CMD ["python", "-m", "linear_hermes"]
