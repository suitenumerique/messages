# https://hub.docker.com/_/python
FROM python:3.13.5-slim-bookworm AS base

# Bump this to force an update of the apt repositories
ENV MIN_UPDATE_DATE="2025-07-14"

WORKDIR /app

RUN apt-get update && apt-get upgrade && apt-get install -y --no-install-recommends \
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

# ---- Poetry package manager and dev system deps ----
FROM base AS poetry

ENV POETRY_NO_INTERACTION=1
ENV POETRY_VIRTUALENVS_CREATE=0
ENV POETRY_VIRTUALENVS_OPTIONS_NO_PIP=1
ENV POETRY_VERSION=2.1.3

RUN python -m pip install poetry==${POETRY_VERSION}

# ---- Base image with dependencies installed ----
FROM poetry AS base-with-deps

COPY pyproject.toml poetry.lock ./

# Create a runtime virtual environment and activate it
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"
ENV VIRTUAL_ENV=/venv
ENV VIRTUAL_ENV_PROMPT=venv

# Install dependencies in this new virtual environment
RUN poetry install --compile

# ---- Base image with dependencies installed for development ----
FROM base-with-deps AS base-with-deps-dev

RUN poetry install --compile --extras dev

# Create and activate virtual environment
RUN python3 -m venv /venv
ENV PATH="/venv/bin:$PATH"
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
