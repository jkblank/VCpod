FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock /app/
COPY services/common /app/services/common
COPY services/library-manager /app/services/library-manager

RUN uv sync --frozen --no-dev --package library-manager

# --frozen --no-dev: without these, a bare `uv run` re-checks the full
# project (incl. the root [dependency-groups].dev entries) on every
# invocation/restart, which needs every workspace member on disk --
# this image only COPYs the few it actually needs. See
# services/fetcher-apple/Dockerfile's ENTRYPOINT comment for the full
# reasoning; confirmed live as a real container crash-loop.
ENTRYPOINT ["uv", "run", "--frozen", "--no-dev", "library-manager"]
