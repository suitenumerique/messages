# Shared Python base image — the single source of truth for the Debian base, the
# uv version, and the uv-managed CPython version used by every uv-based Python
# service (backend, mta-in, and the mta-in pymta variant).
#
# Built + published by `make build-python-base` (locally, tagged
# `messages-python-uv:local`) and by the `python-uv` job in messages-ghcr.yml
# (multi-arch, pushed to ghcr). Services consume it via the build arg
# PYTHON_UV_IMAGE and do `FROM ${PYTHON_UV_IMAGE} AS uv` — see compose.yaml
# `build.args` and the CI `build-args` input.
#
# Deliberately MINIMAL: no build-essential here, because the MTA runtimes inherit
# this image directly and we don't want compilers in production images. Each
# service adds its own build deps in a build-only stage.

FROM debian:trixie-slim@sha256:28de0877c2189802884ccd20f15ee41c203573bd87bb6b883f5f46362d24c5c2 AS base

# Bump to force an apt refresh + security upgrade of the base OS.
ENV MIN_UPDATE_DATE="2026-07-08"

RUN <<EOR
apt-get update
DEBIAN_FRONTEND="noninteractive" apt-get upgrade -y
DEBIAN_FRONTEND="noninteractive" apt-get install -y --no-install-recommends \
    ca-certificates
rm -rf /var/lib/apt/lists/*
EOR

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# ---- uv + managed Python ----
FROM base AS uv

# Pin uv by SHA256 digest for supply chain security.
# Verify with: gh attestation verify --owner astral-sh oci://ghcr.io/astral-sh/uv:0.11.28
COPY --from=ghcr.io/astral-sh/uv@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_PREFERENCE=only-managed
ENV UV_PYTHON_INSTALL_DIR=/opt/python
ENV UV_PROJECT_ENVIRONMENT=/venv

# Install Python via uv — integrity verified against SHA256 checksums embedded in
# the uv binary. python-build-standalone statically links most C deps (ssl, ffi,
# sqlite, zlib, lzma, bz2).
RUN uv python install 3.14.6

# Prune list for the distroless images, shared by the backend and pymta builds
# (they run `strip-python` in a stage off this image). Single source of truth.
COPY --chmod=0755 strip-python.sh /usr/local/bin/strip-python
