# Outbound MTA image (Postfix + SASL relay).
#
# Python comes from the shared uv-managed base (docker/python-uv/Dockerfile:
# debian-trixie + uv 0.11.28 digest-pinned + CPython 3.14.6), NOT the official
# python image — so the interpreter matches the backend and mta-in images.
#
# Global ARG so PYTHON_UV_IMAGE can be used in `FROM`. Built by
# `make build-python-base`; overridden in CI.
ARG PYTHON_UV_IMAGE=messages-python-uv:local

# ---- uv build stage: shared base + build deps ----
FROM ${PYTHON_UV_IMAGE} AS uv

# build-essential only to compile any C-extension wheels during `uv sync`.
# Build-only: it never reaches the runtime image.
RUN <<EOR
apt-get update
DEBIAN_FRONTEND="noninteractive" apt-get install -y --no-install-recommends \
    build-essential
rm -rf /var/lib/apt/lists/*
EOR

# ---- Base image with dependencies installed ----
FROM uv AS base-with-deps

COPY pyproject.toml uv.lock ./

ENV PATH="/venv/bin:$PATH"

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project --no-dev

# ---- Base image with dependencies installed for development ----
FROM base-with-deps AS base-with-deps-dev

RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project --all-extras

ENV PYTHONPATH="/app"
ENV PYTHONIOENCODING=utf-8
ENV PYTHONUNBUFFERED=1


# ---- Base runtime image: shared base (managed Python) + postfix/SASL runtime ----
FROM ${PYTHON_UV_IMAGE} AS runtime-base

# Runtime packages only (ca-certificates comes from the shared base).
RUN <<EOR
apt-get update
DEBIAN_FRONTEND="noninteractive" apt-get install -y --no-install-recommends \
    postfix \
    sasl2-bin \
    libsasl2-modules \
    ssl-cert \
    procps \
    curl \
    dnsutils \
    iproute2 \
    iputils-ping
rm -rf /var/lib/apt/lists/*
EOR

COPY ./entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Create directories for Postfix queues and SASL socket area
RUN mkdir -p /etc/postfix/sasl /var/spool/postfix/private /var/spool/postfix/public

# Ensure self-signed certificates are available (can be overridden by env vars/mounts)
RUN make-ssl-cert generate-default-snakeoil

# We don't need the default sasldb2 file, we use the one in the chroot jail.
RUN rm -rf /etc/sasldb2

ENV PATH="/venv/bin:$PATH"
ENV VIRTUAL_ENV=/venv
ENV VIRTUAL_ENV_PROMPT=venv

ENTRYPOINT [ "/usr/local/bin/entrypoint.sh" ]

# ---- Base runtime image for development ----
FROM runtime-base AS runtime-dev

COPY --from=base-with-deps-dev /venv /venv

# /app will be mounted as a volume in the development container

# ---- Base runtime image for production ----
FROM runtime-base AS runtime-prod

# Hardening: whiteout pip/tkinter/tests/headers from the runtime (see
# strip-python). Not a size change (full Python is in an inherited layer), just
# keeps those tools out of the running container.
RUN strip-python

COPY --from=base-with-deps /venv /venv

COPY ./etc /app/etc
