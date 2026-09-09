FROM python:3.12-slim

# ffmpeg is needed transitively — music-stack-cli imports
# fetcher-apple/fetcher-ytmusic directly (in-process, not a subprocess),
# both of which shell out to it for cover-art handling.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock /app/
COPY services/common /app/services/common
COPY services/fetcher-apple /app/services/fetcher-apple
COPY services/fetcher-ytmusic /app/services/fetcher-ytmusic
COPY services/podcast-manager /app/services/podcast-manager
COPY services/music-stack-cli /app/services/music-stack-cli

RUN uv sync --frozen --no-dev --package music-stack-cli

# --frozen --no-dev: see services/fetcher-apple/Dockerfile's ENTRYPOINT
# comment -- without these, a bare `uv run` re-checks the full project
# on every invocation/restart and crash-loops against this image's
# necessarily-partial services/ COPY. docker-compose.yml's own
# `entrypoint:` override for this service (not `command:`) replaces
# this entirely, not appends to it -- keep both in sync.
ENTRYPOINT ["uv", "run", "--frozen", "--no-dev", "music-stack"]
