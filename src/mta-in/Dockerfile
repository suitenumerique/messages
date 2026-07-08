# Postfix + milter inbound MTA image — counterpart to ./Dockerfile.pymta
# (pure-Python aiosmtpd). This is the production default for now.
#
# Python comes from the shared uv-managed base (deploy/python-uv/Dockerfile:
# debian-trixie + uv 0.11.28 digest-pinned + CPython 3.14.6), NOT the official
# python image — so the interpreter matches the backend and pymta images.
#
# Global ARG so PYTHON_UV_IMAGE can be used in `FROM`. Built by
# `make build-python-base`; overridden in CI.
ARG PYTHON_UV_IMAGE=messages-python-uv:local

# ---- uv build stage: shared base + pymilter build deps ----
FROM ${PYTHON_UV_IMAGE} AS uv

# build-essential + libmilter headers are needed only to compile the pymilter
# C extension (pulled by the `postfix` extra) during `uv sync`. They stay in
# this build-only stage and never reach the runtime image.
RUN <<EOR
apt-get update
DEBIAN_FRONTEND="noninteractive" apt-get install -y --no-install-recommends \
    build-essential \
    libmilter-dev
rm -rf /var/lib/apt/lists/*
EOR

# ---- Base image with dependencies installed ----
FROM uv AS base-with-deps

COPY pyproject.toml uv.lock ./

ENV PATH="/venv/bin:$PATH"

# Install dependencies. The postfix extra pulls in pymilter (C extension needing
# libmilter-dev at install time); it lives behind an extra so the pure-Python
# pymta image can build without libmilter-dev.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project --no-dev --extra postfix

# ---- Base image with dependencies installed for development ----
FROM base-with-deps AS base-with-deps-dev

RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project --all-extras

ENV PYTHONPATH="/app"
ENV PYTHONIOENCODING=utf-8
ENV PYTHONUNBUFFERED=1


# ---- Base runtime image: shared base (managed Python) + postfix runtime deps ----
FROM ${PYTHON_UV_IMAGE} AS runtime-base

# Runtime packages only — no build-essential/libmilter-dev. libmilter1.0.1 is
# the runtime shared library the compiled pymilter extension links against.
# (ca-certificates comes from the shared base.)
RUN <<EOR
apt-get update
DEBIAN_FRONTEND="noninteractive" apt-get install -y --no-install-recommends \
    postfix \
    postfix-pcre \
    curl \
    procps \
    net-tools \
    mailutils \
    libmilter1.0.1 \
    iproute2 \
    iputils-ping
rm -rf /var/lib/apt/lists/*
EOR

COPY ./entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV PATH="/venv/bin:$PATH"
ENV VIRTUAL_ENV=/venv
ENV VIRTUAL_ENV_PROMPT=venv

ENTRYPOINT [ "/usr/local/bin/entrypoint.sh" ]

# ---- Base runtime image for development ----
FROM runtime-base AS runtime-dev

COPY --from=base-with-deps-dev /venv /venv

# The package lives at /app/src (mounted as a volume in dev); put it on the path
# so the shared test suite can `import pymta` — matching Dockerfile.pymta.
ENV PYTHONPATH="/app/src"

# /app will be mounted as a volume in the development container

# ---- Base runtime image for production ----
FROM runtime-base AS runtime-prod

# Hardening: whiteout pip/tkinter/tests/headers from the runtime (see
# strip-python). Not a size change (full Python is in an inherited layer), just
# keeps those tools out of the running container.
RUN strip-python

COPY --from=base-with-deps /venv /venv

COPY ./src /app/src
COPY ./etc /app/etc
