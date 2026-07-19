FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock /app/
COPY services/common /app/services/common
COPY services/library-manager /app/services/library-manager

RUN uv sync --frozen --no-dev --package library-manager

ENTRYPOINT ["uv", "run", "library-manager"]
