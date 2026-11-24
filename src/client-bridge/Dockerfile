# Client bridge image: IMAP + SMTP submission for email clients, backed by the
# Messages API.
#
# Python comes from the shared uv-managed base (deploy/python-uv/Dockerfile:
# debian-trixie + uv digest-pinned + CPython pinned), like the backend and
# pymta images. Global ARG so it can be used in `FROM`. Built by
# `make build-python-base`; overridden in CI.
ARG PYTHON_UV_IMAGE=messages-python-uv:local

# ---- uv stage, also used by `make deps-lock-client-bridge` ----
FROM ${PYTHON_UV_IMAGE} AS uv


# ---- Production dependencies ----
FROM uv AS base-with-deps

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project --no-dev


# ---- Development dependencies ----
FROM base-with-deps AS base-with-deps-dev

RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project --extra dev


# ---- Base runtime image ----
FROM ${PYTHON_UV_IMAGE} AS runtime-base

COPY --chmod=0755 ./entrypoint.sh /usr/local/bin/entrypoint.sh

ENV PATH="/venv/bin:$PATH"
ENV VIRTUAL_ENV=/venv
ENV VIRTUAL_ENV_PROMPT=venv
ENV PYTHONPATH="/app"

ENTRYPOINT [ "/usr/local/bin/entrypoint.sh" ]


# ---- Development runtime ----
# /app is mounted as a volume in the development container.
FROM runtime-base AS runtime-dev

COPY --from=base-with-deps-dev /venv /venv


# ---- Production runtime ----
FROM runtime-base AS runtime-prod

# Hardening: whiteout pip/tkinter/tests/headers from the runtime (see
# deploy/python-uv/strip-python.sh).
RUN strip-python

COPY --from=base-with-deps /venv /venv
COPY ./src /app/src
# COPY keeps the build host's file modes; make sure the unprivileged user can read.
RUN chmod -R a+rX /app/src

ARG DOCKER_USER=65532
USER ${DOCKER_USER}

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD ["python", "-m", "src.healthcheck"]
