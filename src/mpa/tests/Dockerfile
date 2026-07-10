# Test harness for the rspamd MPA. Inherits the shared python-uv base
# (managed CPython 3.14.6 + uv); built by `make build-python-base`.
ARG PYTHON_UV_IMAGE=messages-python-uv:local
FROM ${PYTHON_UV_IMAGE}

WORKDIR /app

# The shared base ships uv (no bundled pip on PATH); install into a venv.
ENV VIRTUAL_ENV=/venv
ENV PATH="/venv/bin:$PATH"
COPY requirements.txt .
RUN uv venv /venv && uv pip install --no-cache -r requirements.txt

COPY . .
