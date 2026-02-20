# https://hub.docker.com/_/python
FROM python:3.13.7-slim-trixie AS base

# Bump this to force an update of the apt repositories
ENV MIN_UPDATE_DATE="2025-12-01"

RUN <<EOR
apt-get update
DEBIAN_FRONTEND="noninteractive" apt-get upgrade -y
rm -rf /var/lib/apt/lists/*
EOR

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# ---- uv package manager and dev system deps ----
FROM base AS uv

COPY --from=ghcr.io/astral-sh/uv:0.9.10 /uv /uvx /bin/

RUN <<EOR
apt-get update
DEBIAN_FRONTEND="noninteractive" apt-get install -y --no-install-recommends \
    curl \
    git \
    rdfind \
    libmagic1 \
    build-essential \
    python3-dev \
    zlib1g-dev
rm -rf /var/lib/apt/lists/*
EOR

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=0
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

# ---- static link collector ----
FROM base-with-deps AS link-collector
ARG MESSAGES_STATIC_ROOT=/data/static

# Copy messages application (see .dockerignore)
COPY . /app/

WORKDIR /app

# collectstatic
RUN <<EOR
DJANGO_CONFIGURATION=Build python manage.py collectstatic --noinput
# Replace duplicated file by a symlink to decrease the overall size of the final image
rdfind -makesymlinks true -followsymlinks true -makeresultsfile false ${MESSAGES_STATIC_ROOT}
EOR

# ---- Base runtime image ----
FROM base AS runtime-base

# Give the "root" group the same permissions as the "root" user on /etc/passwd
# to allow a user belonging to the root group to add new users; typically the
# docker user (see entrypoint).
RUN chmod g=u /etc/passwd

# Install required packages
RUN <<EOR
apt-get update
DEBIAN_FRONTEND="noninteractive" apt-get install -y --no-install-recommends \
  gettext \
  libmagic1
rm -rf /var/lib/apt/lists/*
EOR

# Un-privileged user running the application
ARG DOCKER_USER
USER ${DOCKER_USER}

ENV PATH="/venv/bin:$PATH"
ENV VIRTUAL_ENV=/venv
ENV VIRTUAL_ENV_PROMPT=venv

# We wrap commands run in this container by the following entrypoint that
# creates a user on-the-fly with the container user ID (see USER) and root group
# ID.
COPY ./entrypoint /usr/local/bin/entrypoint
ENTRYPOINT [ "/usr/local/bin/entrypoint" ]


# ---- Base runtime image for development ----
FROM runtime-base AS runtime-dev

COPY --from=base-with-deps-dev /venv /venv

# /app will be mounted as a volume in the development container

# Target database host (e.g. database engine following docker compose services
# name) & port
# ENV DB_HOST=postgresql \
#     DB_PORT=5432

# Run django development server
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]


# ---- Base runtime image for production ----
FROM runtime-base AS runtime-prod
ARG MESSAGES_STATIC_ROOT=/data/static

COPY --from=base-with-deps /venv /venv
COPY --from=link-collector ${MESSAGES_STATIC_ROOT} ${MESSAGES_STATIC_ROOT}
COPY . /app/

# The default command runs gunicorn WSGI server in messages's main module
CMD ["gunicorn", "-c", "/app/gunicorn.conf.py", "messages.wsgi:application"]

HEALTHCHECK --interval=30s --timeout=2s --start-period=30s \
  CMD ["python", "-c", "import os; from urllib.request import Request, urlopen; urlopen(Request('http://localhost:8000/__heartbeat__/', headers={'Host': os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost').split(',')[0].strip(), 'X-Forwarded-Proto': 'https'}), timeout=2)"]
