FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock /app/
COPY services/common /app/services/common
COPY services/fetcher-apple /app/services/fetcher-apple

RUN uv sync --frozen --no-dev --package fetcher-apple

# --frozen --no-dev here too, not just above: a bare `uv run` re-checks
# the environment against the *full* project on every invocation
# (including every container restart), which needs every workspace
# member's pyproject.toml on disk to validate the root [dependency-
# groups].dev entries -- but this image only ever COPYs the few this
# service actually depends on. Confirmed live: without these flags the
# container crash-loops on startup with "X references a workspace ...
# but is not a workspace member" for whichever member wasn't copied in,
# even though the RUN line above (which already has --frozen --no-dev)
# builds successfully.
ENTRYPOINT ["uv", "run", "--frozen", "--no-dev", "fetcher-apple"]
