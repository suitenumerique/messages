# Minimal test image for the jmap-email package.
# Zero non-stdlib runtime deps; only pytest + hypothesis for the suite,
# plus ``ty`` for static type-checking.
FROM python:3.14.5-slim

WORKDIR /app

# ``ty`` ships as a single Rust binary on PyPI — no Node, no native
# libs, no bootstrap step. Pin pytest / hypothesis / ty so the image
# is deterministic; ``--no-cache-dir`` keeps the layer small.
RUN pip install --no-cache-dir \
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

# Install the package itself (no runtime deps; zero new packages
# resolved). Editable so that the mount-overlay in development
# instantly picks up source edits.
RUN pip install --no-cache-dir -e .

# Smoke import on build to catch package-layout breakage early.
RUN python -c "import jmap_email; print('jmap-email', jmap_email.__version__)"

CMD ["pytest", "-q"]
