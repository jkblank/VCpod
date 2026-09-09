FROM python:3.12-slim

# ffmpeg is needed transitively — fetch-scheduler imports music-stack-cli,
# which imports fetcher-apple/fetcher-ytmusic, both of which shell out to it.
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
# library-manager: fetch-scheduler's own pyproject.toml depends on it
# directly (automatic dedup/cleanup wired into each tick, see notes.md)
# -- uv sync --frozen below needs its pyproject.toml on disk to resolve
# that workspace source, even though fetch-scheduler never imports it
# through music-stack-cli.
COPY services/library-manager /app/services/library-manager
COPY services/fetch-scheduler /app/services/fetch-scheduler

RUN uv sync --frozen --no-dev --package fetch-scheduler

ENTRYPOINT ["uv", "run", "fetch-scheduler"]
