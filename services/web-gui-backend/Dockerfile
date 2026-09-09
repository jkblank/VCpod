# Two-stage build: the frontend's static assets get baked into the same
# image as the backend (see README.md's "Running it as one process") so
# a homelab deploy is one container, one port, no separate frontend
# server.
#
# This image is deliberately heavier than every other service's
# Dockerfile in this repo (docker-compose.yml) -- it also vendors a full,
# independent sync-orchestrator install (its own venv, its own
# iopenpod/PyQt6 dependency tree) so the Sync screen's "identify
# device"/"compute plan"/"execute" buttons work the same way they do
# bare-metal: by shelling out to `sync-orchestrator` as a subprocess
# (web_gui_backend/device.py, sync_runner.py). Needs to run --privileged
# with real device/D-Bus access at runtime -- see docker-compose.yml's
# web-gui-backend service for the full explanation and notes.md's
# "Distribution: why sync-orchestrator isn't containerized too" for the
# constraint this works around.

# --- frontend build ----------------------------------------------------
FROM node:22-slim AS frontend-build
WORKDIR /app
COPY services/web-gui-frontend/package.json services/web-gui-frontend/package-lock.json ./
RUN npm ci
COPY services/web-gui-frontend/ ./
RUN npm run build

# --- backend + vendored sync-orchestrator -------------------------------
FROM python:3.12-slim

# ffmpeg: transitively needed the same way fetch-scheduler's Dockerfile
# already documents (web-gui-backend depends on fetcher-apple/
# fetcher-ytmusic/podcast-manager/audiobook-manager, all of which shell
# out to it).
# udisks2: gives sync-orchestrator's `udisksctl` client binary for its
# best-effort auto-mount (device.py's mount_candidate_devices) -- talks
# to whichever udisks2 *daemon* is reachable over the D-Bus system
# socket bind-mounted in at runtime, not one running in this container.
# util-linux: `eject`, run after a successful --execute.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg udisks2 util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Root workspace member (common/fetcher-apple/fetcher-ytmusic/
# podcast-manager/audiobook-manager/web-gui-backend) -- same shape as
# every other Dockerfile here.
COPY pyproject.toml uv.lock /app/
COPY services/common /app/services/common
COPY services/fetcher-apple /app/services/fetcher-apple
COPY services/fetcher-ytmusic /app/services/fetcher-ytmusic
COPY services/podcast-manager /app/services/podcast-manager
COPY services/audiobook-manager /app/services/audiobook-manager
COPY services/web-gui-backend /app/services/web-gui-backend
RUN uv sync --frozen --no-dev --package web-gui-backend

# sync-orchestrator is a standalone uv project on purpose (its own
# lockfile, its own venv) -- device.py/sync_runner.py invoke it as a
# `uv run --project services/sync-orchestrator sync-orchestrator ...`
# subprocess, exactly like they already do against a bare-metal
# checkout. Vendoring a second, independent venv for it here (rather
# than merging it into the workspace above) is what makes that
# subprocess call work unmodified from inside this container.
# --frozen at build time means the *first* real click of "identify
# device"/"sync now" in the browser doesn't stall on a cold `uv sync`
# over the network.
COPY services/sync-orchestrator /app/services/sync-orchestrator
RUN uv sync --project services/sync-orchestrator --frozen --no-dev

# Built by the frontend-build stage above -- app.py's
# default_frontend_dist() resolves this exact sibling path
# (services/web-gui-frontend/dist, next to services/web-gui-backend) on
# its own, so no --frontend-dist flag is needed at runtime.
COPY --from=frontend-build /app/dist /app/services/web-gui-frontend/dist

EXPOSE 8420
# --frozen --no-dev: see services/fetcher-apple/Dockerfile's ENTRYPOINT
# comment -- without these, a bare `uv run` re-checks the full root
# workspace project (incl. members not needed/COPY'd here, e.g.
# library-manager/fetch-scheduler/music-stack-cli) on every invocation/
# restart and crash-loops.
ENTRYPOINT ["uv", "run", "--frozen", "--no-dev", "web-gui-backend"]
