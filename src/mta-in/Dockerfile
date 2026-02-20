# https://hub.docker.com/_/python
FROM python:3.14.3-slim-trixie AS base

# Bump this to force an update of the apt repositories
ENV MIN_UPDATE_DATE="2026-02-20"

WORKDIR /app

RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    ca-certificates \
    postfix \
    postfix-pcre \
    curl \
    procps \
    net-tools \
    mailutils \
    libmilter-dev \
    build-essential \
    iproute2 \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# ---- uv package manager ----
FROM base AS uv

COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=never
ENV UV_PROJECT_ENVIRONMENT=/venv

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


# ---- Base runtime image ----
FROM base AS runtime-base

COPY ./entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

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

COPY --from=base-with-deps /venv /venv

COPY ./src /app/src
COPY ./etc /app/etc
