# Shared base image (deploy/python-uv/Dockerfile): debian-trixie + apt upgrade +
# ca-certificates + uv 0.11.28 (digest-pinned) + uv-managed CPython 3.14.6 at
# /opt/python. Global ARG so it can be used in `FROM` below. Built by
# `make build-python-base`; overridden in CI.
ARG PYTHON_UV_IMAGE=messages-python-uv:local

# ---- uv + managed Python + build system deps ----
FROM ${PYTHON_UV_IMAGE} AS uv

# Backend-specific build/collectstatic deps (libmagic1 is needed at build time
# because collectstatic imports the app, which imports python-magic).
RUN <<EOR
apt-get update
DEBIAN_FRONTEND="noninteractive" apt-get install -y --no-install-recommends \
    curl \
    git \
    rdfind \
    libmagic1 \
    build-essential \
    zlib1g-dev
rm -rf /var/lib/apt/lists/*
EOR

# ---- Production dependencies ----
FROM uv AS base-with-deps

COPY pyproject.toml uv.lock ./

ENV PATH="/venv/bin:$PATH"

RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project --no-editable --exact --no-dev

# ---- Development dependencies ----
FROM base-with-deps AS base-with-deps-dev

RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project --no-editable --all-extras

# ---- Static file collector ----
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
# Inherits the shared base directly (full managed Python at /opt/python).
FROM ${PYTHON_UV_IMAGE} AS runtime-base

# Give the "root" group the same permissions as the "root" user on /etc/passwd
# to allow a user belonging to the root group to add new users; typically the
# docker user (see entrypoint).
RUN chmod g=u /etc/passwd

# Install required packages
RUN <<EOR
apt-get update
DEBIAN_FRONTEND="noninteractive" apt-get install -y --no-install-recommends \
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
COPY --chmod=755 ./entrypoint /usr/local/bin/entrypoint
ENTRYPOINT [ "/usr/local/bin/entrypoint" ]


# ---- Development runtime ----
FROM runtime-base AS runtime-dev

# Full managed Python (with headers, pip) is already present from the shared base.
COPY --from=base-with-deps-dev /venv /venv

# /app will be mounted as a volume in the development container

# Run django development server
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]


# ---- Production application source (strip tests, dev tooling, build files) ----
FROM ${PYTHON_UV_IMAGE} AS app-prod
COPY . /app/
RUN rm -rf \
    /app/core/tests \
    /app/core/factories.py \
    /app/e2e \
    /app/Dockerfile \
    /app/.pylintrc \
    /app/pyproject.toml \
    /app/uv.lock \
    /app/README.md \
    /app/entrypoint


# ---- Production runtime ----
FROM runtime-base AS runtime-prod
ARG DOCKER_USER
ARG MESSAGES_STATIC_ROOT=/data/static

# Hardening: whiteout pip/tkinter/tests/headers from the runtime (see
# strip-python). This does NOT shrink the image — the full Python sits in an
# inherited layer — but keeps those tools out of the running container. Strip as
# root so /opt/python stays root-owned (not writable by the unprivileged app
# user), then restore that user.
USER root
RUN strip-python
USER ${DOCKER_USER}

# Full managed Python is inherited from the shared base (runtime-base). The
# distroless target below copies in an already-stripped Python instead.
COPY --from=base-with-deps /venv /venv
COPY --from=link-collector ${MESSAGES_STATIC_ROOT} ${MESSAGES_STATIC_ROOT}
COPY --from=app-prod /app/ /app/

# The default command runs gunicorn WSGI server in messages's main module
CMD ["gunicorn", "-c", "/app/gunicorn.conf.py", "messages.wsgi:application"]

HEALTHCHECK --interval=30s --timeout=2s --start-period=30s \
  CMD ["python", "-c", "import os; from urllib.request import Request, urlopen; urlopen(Request('http://localhost:8000/__heartbeat__/', headers={'Host': os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost').split(',')[0].strip(), 'X-Forwarded-Proto': 'https'}), timeout=2)"]


# ---- Collect shared libraries for distroless ----
# libmagic + transitive deps (liblzma, libbz2) are the only shared libs needed.
# Python's own deps (ffi, sqlite, ssl, zlib) are statically linked in python-build-standalone.
FROM uv AS shared-libs
RUN mkdir -p /shared-libs/usr/lib /shared-libs/usr/share/misc && \
    for lib in libmagic liblzma libbz2; do \
        cp -L /usr/lib/*/${lib}.so* /shared-libs/usr/lib/; \
    done && \
    cp /usr/share/misc/magic.mgc /shared-libs/usr/share/misc/


# ---- Strip Python for the distroless image (see deploy/python-uv/strip-python.sh) ----
# Only runtime-distroless-prod (the real production target) uses this. The slim
# runtime-prod inherits the full managed Python from the shared base instead.
FROM ${PYTHON_UV_IMAGE} AS python-stripped-runtime
RUN strip-python


# ---- Distroless production runtime ----
# Uses cc-debian13 (C runtime only) + python-build-standalone from uv.
# Debug with: docker run --entrypoint='' gcr.io/distroless/cc-debian13:debug-nonroot sh
FROM gcr.io/distroless/cc-debian13:nonroot AS runtime-distroless-prod
ARG MESSAGES_STATIC_ROOT=/data/static

WORKDIR /app

# Stripped Python installation (python-build-standalone via uv)
COPY --from=python-stripped-runtime /opt/python /opt/python
# Python dependencies
COPY --from=base-with-deps /venv /venv
# libmagic shared library + magic database
COPY --from=shared-libs /shared-libs/ /
# Static files
COPY --from=link-collector ${MESSAGES_STATIC_ROOT} ${MESSAGES_STATIC_ROOT}
# Application code
COPY --from=app-prod /app/ /app/

ENV PATH="/venv/bin:$PATH"
ENV VIRTUAL_ENV=/venv
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# Distroless has no ldconfig — tell the dynamic linker where to find libmagic
ENV LD_LIBRARY_PATH=/usr/lib

CMD ["gunicorn", "-c", "/app/gunicorn.conf.py", "messages.wsgi:application"]

HEALTHCHECK --interval=30s --timeout=2s --start-period=30s \
  CMD ["python", "-c", "import os; from urllib.request import Request, urlopen; urlopen(Request('http://localhost:8000/__heartbeat__/', headers={'Host': os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost').split(',')[0].strip(), 'X-Forwarded-Proto': 'https'}), timeout=2)"]
