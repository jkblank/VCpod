FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app/services/fetcher-spotify
COPY services/common /app/services/common
COPY services/fetcher-spotify /app/services/fetcher-spotify

RUN uv sync --frozen --no-dev

ENTRYPOINT ["uv", "run", "fetcher-spotify"]
