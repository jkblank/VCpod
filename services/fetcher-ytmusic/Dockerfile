FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock /app/
COPY services/common /app/services/common
COPY services/fetcher-ytmusic /app/services/fetcher-ytmusic

RUN uv sync --frozen --no-dev --package fetcher-ytmusic

# --frozen --no-dev: see services/fetcher-apple/Dockerfile's ENTRYPOINT
# comment -- without these, a bare `uv run` re-checks the full project
# on every invocation/restart and crash-loops against this image's
# necessarily-partial services/ COPY.
ENTRYPOINT ["uv", "run", "--frozen", "--no-dev", "fetcher-ytmusic"]
