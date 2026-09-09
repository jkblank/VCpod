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

ENTRYPOINT ["uv", "run", "music-stack"]
