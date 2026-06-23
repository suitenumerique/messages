# Realtime SSE relay — standalone, pure-Python (Starlette + uvicorn + redis).
# Production image is distroless + non-root, mirroring the backend.

# ---- Base OS ----
FROM debian:trixie-slim AS base

# Bump this to force an update of the apt repositories
ENV MIN_UPDATE_DATE="2026-02-20"

RUN apt-get update \
    && DEBIAN_FRONTEND="noninteractive" apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# ---- uv + managed Python ----
FROM base AS uv

# Pin uv by SHA256 digest for supply chain security.
# Verify with: gh attestation verify --owner astral-sh oci://ghcr.io/astral-sh/uv:0.11.19
COPY --from=ghcr.io/astral-sh/uv@sha256:b46b03ddfcfbf8f547af7e9eaefdf8a39c8cebcba7c98858d3162bd28cf536f6 /uv /uvx /bin/

RUN apt-get update \
    && DEBIAN_FRONTEND="noninteractive" apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_PREFERENCE=only-managed
ENV UV_PYTHON_INSTALL_DIR=/opt/python
ENV UV_PROJECT_ENVIRONMENT=/venv

# python-build-standalone: ssl/ffi/sqlite/zlib are statically linked, so the
# distroless image below needs no extra shared libs.
RUN uv python install 3.14.5

# ---- Production dependencies ----
FROM uv AS base-with-deps

COPY pyproject.toml uv.lock ./

ENV PATH="/venv/bin:$PATH"

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-editable --exact --no-dev

# ---- Development dependencies (tests) ----
FROM base-with-deps AS base-with-deps-dev

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-editable --all-extras

ENV PYTHONPATH="/app"

# ---- Strip Python for production (drop pip, idle, tkinter, tcl, headers, tests) ----
FROM uv AS python-runtime
RUN set -e; \
    PYDIR=$(dirname $(dirname $(uv python find 3.14.5))); \
    rm -rf \
        $PYDIR/bin/idle* $PYDIR/bin/pip* $PYDIR/bin/pydoc* $PYDIR/bin/*-config \
        $PYDIR/include $PYDIR/share \
        $PYDIR/lib/pkgconfig $PYDIR/lib/itcl* $PYDIR/lib/libtcl* \
        $PYDIR/lib/tcl* $PYDIR/lib/tk* $PYDIR/lib/thread* \
        $PYDIR/lib/python3.14/idlelib \
        $PYDIR/lib/python3.14/ensurepip \
        $PYDIR/lib/python3.14/tkinter \
        $PYDIR/lib/python3.14/turtledemo \
        $PYDIR/lib/python3.14/lib-dynload/_tkinter* \
        $PYDIR/lib/python3.14/lib-dynload/_ctypes_test* \
        /opt/python/.gitignore /opt/python/.lock /opt/python/.temp

# ---- Development runtime (Debian: shell + pip for debugging; code mounted) ----
FROM base AS runtime-dev

COPY --from=uv /opt/python /opt/python
COPY --from=base-with-deps-dev /venv /venv

ENV PATH="/venv/bin:$PATH"
ENV VIRTUAL_ENV=/venv
ENV PYTHONPATH="/app"

# /app/realtime_relay is mounted in dev (see compose.yaml). compose overrides the
# command for tests; the default runs the relay.
CMD ["uvicorn", "realtime_relay.app:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "75", "--no-access-log"]

# ---- Production runtime (distroless, non-root) ----
# cc-debian13 (C runtime only) + python-build-standalone from uv. No shell, no
# package manager, runs as the image's built-in nonroot uid.
# Debug with: docker run --entrypoint='' gcr.io/distroless/cc-debian13:debug-nonroot sh
FROM gcr.io/distroless/cc-debian13:nonroot AS runtime-prod

WORKDIR /app

COPY --from=python-runtime /opt/python /opt/python
COPY --from=base-with-deps /venv /venv
# Application code only (tests excluded via .dockerignore).
COPY ./realtime_relay /app/realtime_relay

ENV PATH="/venv/bin:$PATH"
ENV VIRTUAL_ENV=/venv
ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# No shell in distroless: the port is read from $PORT inside __main__.
CMD ["python", "-m", "realtime_relay"]
