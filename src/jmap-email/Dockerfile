# Test image for the jmap-email package. Inherits the shared python-uv base
# (managed CPython 3.14.6 + uv). NOTE: the PyPI *release* still builds on the
# official python:3.14.6-slim image via bin/release-jmap-email.sh — that is the
# release-parity environment; this image is only for CI lint/type/test.
# One runtime dep (idna); plus pytest + hypothesis for the suite, and
# ``ty``/``ruff``/``pylint`` for type-checking and linting.
ARG PYTHON_UV_IMAGE=messages-python-uv:local
FROM ${PYTHON_UV_IMAGE}

WORKDIR /app

# The shared base ships uv (no bundled pip on PATH); create a venv on the managed
# Python and install the pinned dev tools into it. ``ty`` ships as a single Rust
# binary on PyPI — no Node, no native libs.
ENV VIRTUAL_ENV=/venv
ENV PATH="/venv/bin:$PATH"
RUN uv venv /venv \
    && uv pip install --no-cache \
    "pytest==9.0.2" \
    "pytest-cov==7.0.0" \
    "pytest-xdist==3.8.0" \
    "hypothesis==6.151.9" \
    "pylint==4.0.4" \
    "ruff==0.15.0" \
    "ty==0.0.44"

# Copy the package + tests. Mount-overlay during development.
COPY pyproject.toml README.md LICENSE CHANGELOG.md ./
COPY jmap_email ./jmap_email
COPY tests ./tests

# Install the package itself (resolves its one runtime dep, idna).
# Editable so that the mount-overlay in development instantly picks up edits.
RUN uv pip install --no-cache -e .

# Smoke import on build to catch package-layout breakage early.
RUN python -c "import jmap_email; print('jmap-email', jmap_email.__version__)"

CMD ["pytest", "-q"]
