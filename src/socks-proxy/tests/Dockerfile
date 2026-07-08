# Test harness for the socks-proxy (dante). Inherits the shared python-uv base
# (managed CPython 3.14.6 + uv); built by `make build-python-base`.
ARG PYTHON_UV_IMAGE=messages-python-uv:local
FROM ${PYTHON_UV_IMAGE}

# netcat for the proxy connectivity checks.
RUN apt-get update && apt-get install -y --no-install-recommends \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The shared base ships uv (no bundled pip on PATH); install into a venv.
ENV VIRTUAL_ENV=/venv
ENV PATH="/venv/bin:$PATH"
COPY requirements.txt .
RUN uv venv /venv && uv pip install --no-cache -r requirements.txt

COPY . .

CMD ["pytest", "-v"]
