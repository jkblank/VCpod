FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock /app/
COPY services/common /app/services/common
COPY services/podcast-manager /app/services/podcast-manager

RUN uv sync --frozen --no-dev --package podcast-manager

# --frozen --no-dev: see services/fetcher-apple/Dockerfile's ENTRYPOINT
# comment -- without these, a bare `uv run` re-checks the full project
# on every invocation/restart and crash-loops against this image's
# necessarily-partial services/ COPY.
ENTRYPOINT ["uv", "run", "--frozen", "--no-dev", "podcast-manager"]
