# check=skip=SecretsUsedInArgOrEnv
# Image for Anthropic's OSS Scanner. See .oss-scanner/threat_model.md.
# The scan runs with no network, so this build installs every dependency and
# the services the test suites need: PostgreSQL, Redis, an S3 endpoint and a
# mail catcher.
# Scanner contract: the repository is the build context, the checkout is /src.
#
# The same digest as deploy/python-uv/Dockerfile, the base of the project's own
# images.
FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a

# initdb reads the locale when the postgresql package creates its cluster.
# Without a UTF-8 locale the cluster gets the SQL_ASCII encoding.
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1

# policy-rc.d stops the postgresql and redis packages from starting a server:
# a server killed at the end of a layer leaves a stale pid file in the image.
# build-essential, zlib1g-dev and libmilter-dev compile the C extensions of the
# lock files, as src/backend/Dockerfile and src/mta-in/Dockerfile do.
RUN <<EOR
set -eux
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod +x /usr/sbin/policy-rc.d
apt-get update
apt-get upgrade -y
apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    git \
    libmagic1 \
    libmilter-dev \
    libnss-myhostname \
    netcat-openbsd \
    openssl \
    postgresql-17 \
    python3 \
    python3-venv \
    redis-server \
    zlib1g-dev
rm -f /usr/sbin/policy-rc.d
rm -rf /var/lib/apt/lists/*
# settings.py resolves the hostname at import time. With no network and no
# DNS, only the myhostname NSS module makes that lookup succeed.
sed -i 's/^hosts:.*/hosts: files myhostname dns/' /etc/nsswitch.conf
# core/migrations/0001 creates this extension.
test -f /usr/share/postgresql/17/extension/unaccent.control
pg_lsclusters
EOR

# The cluster holds test data only. postgresql.conf includes conf.d.
RUN printf '%s\n' \
      "listen_addresses = 'localhost'" \
      "fsync = off" \
      "full_page_writes = off" \
      "synchronous_commit = off" \
      "max_connections = 200" \
      > /etc/postgresql/17/main/conf.d/oss-scanner.conf

# The dev stack uses rustfs for S3. moto is the stand-in here: it is pure Python
# and needs no architecture-specific binary. It runs on the Debian interpreter,
# in a virtual environment of its own, so it never meets the project's pins.
RUN <<EOR
set -eux
python3 -m venv /opt/s3
/opt/s3/bin/pip install --no-cache-dir --disable-pip-version-check \
    "moto[server]==5.2.3" "boto3==1.43.111"
/opt/s3/bin/moto_server --help >/dev/null
EOR

# The scanner replaces ENTRYPOINT and CMD, so the image cannot start the
# services itself. svcctl starts them, and every shell sources the init file.
COPY --chmod=0755 <<'EOF' /usr/local/bin/svcctl
#!/bin/sh
set -eu

pg_ready() {
    pg_isready -q -h 127.0.0.1 -p 5432
}

redis_ready() {
    redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1
}

s3_ready() {
    nc -zw1 127.0.0.1 9000 >/dev/null 2>&1
}

mail_ready() {
    nc -zw1 127.0.0.1 1025 >/dev/null 2>&1
}

wait_for() {
    i=0
    while [ "$i" -lt 60 ]; do
        if "$1"; then return 0; fi
        sleep 0.5
        i=$((i + 1))
    done
    echo "svcctl: $2 did not become ready" >&2
    return 1
}

start_postgresql() {
    # pg_isready exits 0 when the server accepts connections, 1 while it is
    # still starting, and 2 when nothing answers.
    state=0
    pg_ready || state=$?
    if [ "$state" -eq 0 ]; then return 0; fi
    install -d -o postgres -g postgres -m 2775 /var/run/postgresql
    install -d -o postgres -g postgres -m 1775 /var/log/postgresql
    if [ "$state" -eq 1 ]; then
        # Another shell starts the server now.
        wait_for pg_ready postgresql
        return 0
    fi
    # Nothing answers, so the pid file that the scanner's own layer left
    # behind is stale. A kill -0 test is not enough: another process can hold
    # the same pid.
    rm -f /var/lib/postgresql/17/main/postmaster.pid
    pg_ctlcluster 17 main start
    wait_for pg_ready postgresql
}

start_redis() {
    if redis_ready; then return 0; fi
    install -d -o redis -g redis -m 2775 /var/run/redis
    redis-server /etc/redis/redis.conf --daemonize yes --save "" --appendonly no
    wait_for redis_ready redis
}

start_s3() {
    if s3_ready; then return 0; fi
    nohup /opt/s3/bin/moto_server -H 127.0.0.1 -p 9000 >/var/log/moto_server.log 2>&1 &
    wait_for s3_ready "the s3 endpoint"
    # moto keeps the buckets in memory, so every start creates them again.
    /opt/s3/bin/python - <<'PY'
import os

import boto3
from botocore.exceptions import ClientError

client = boto3.client(
    "s3",
    endpoint_url=os.environ["AWS_S3_ENDPOINT_URL"],
    aws_access_key_id=os.environ["AWS_S3_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_S3_SECRET_ACCESS_KEY"],
    region_name="us-east-1",
)
for name in (
    "AWS_STORAGE_BUCKET_NAME",
    "STORAGE_MESSAGE_IMPORTS_BUCKET_NAME",
    "STORAGE_MESSAGE_BLOBS_BUCKET_NAME",
):
    try:
        client.head_bucket(Bucket=os.environ[name])
    except ClientError:
        client.create_bucket(Bucket=os.environ[name])
PY
}

start_maildev() {
    # The outbound tests resolve the host name mailcatcher, the service name
    # of compose.yaml. Docker writes /etc/hosts at the start of the container,
    # so the name is added here and not in the image. The file is read-only
    # during the build, so the two tests fail there.
    if ! grep -qE '[[:space:]]mailcatcher$' /etc/hosts; then
        echo '127.0.0.1 mailcatcher' >> /etc/hosts 2>/dev/null || true
    fi
    if mail_ready; then return 0; fi
    nohup maildev --ip 127.0.0.1 --smtp 1025 --web-ip 127.0.0.1 --web 1080 \
        >/var/log/maildev.log 2>&1 &
    wait_for mail_ready maildev
}

case "${1:-start}" in
start)
    start_postgresql
    start_redis
    start_s3
    # maildev serves two tests, so its failure must not stop the others.
    start_maildev || echo "svcctl: maildev did not start, see /var/log/maildev.log" >&2
    ;;
stop)
    pg_ctlcluster 17 main stop || true
    redis-cli -h 127.0.0.1 -p 6379 shutdown nosave >/dev/null 2>&1 || true
    pkill -f /opt/s3/bin/moto_server || true
    pkill -f -- 'maildev --ip' || true
    ;;
status)
    pg_lsclusters
    if redis_ready; then echo "redis: up"; else echo "redis: down"; fi
    if s3_ready; then echo "s3: up"; else echo "s3: down"; fi
    if mail_ready; then echo "maildev: up"; else echo "maildev: down"; fi
    ;;
psql)
    shift
    exec runuser -u postgres -- psql "$@"
    ;;
*)
    echo "usage: svcctl [start|stop|status|psql]" >&2
    exit 2
    ;;
esac
EOF

# Sourced by BASH_ENV, /etc/profile.d and /root/.bashrc. It must stay silent
# and must not change the exit status.
COPY --chmod=0644 <<'EOF' /etc/oss-scanner-shell-init
if [ -z "${OSS_SCANNER_SERVICES_STARTED:-}" ]; then
    if /usr/local/bin/svcctl start >/dev/null 2>&1; then
        OSS_SCANNER_SERVICES_STARTED=1
        export OSS_SCANNER_SERVICES_STARTED
    fi
fi
:
EOF

# The pure-Python inbound MTA, started the way the mta-in-py-test service of
# compose.yaml starts it, so the suite in /src/src/mta-in has a server to talk
# to. The suite starts its own mock of the backend on port 8000.
COPY --chmod=0755 <<'EOF' /usr/local/bin/pymtactl
#!/bin/sh
set -eu

smtp_ready() {
    nc -zw1 127.0.0.1 25 >/dev/null 2>&1
}

case "${1:-start}" in
start)
    if smtp_ready; then exit 0; fi
    cd /src/src/mta-in
    nohup /opt/venv/mta-in/bin/python -m pymta.server >/var/log/pymta.log 2>&1 &
    i=0
    while [ "$i" -lt 60 ]; do
        if smtp_ready; then exit 0; fi
        sleep 0.5
        i=$((i + 1))
    done
    echo "pymtactl: pymta did not listen on port 25, see /var/log/pymta.log" >&2
    exit 1
    ;;
stop)
    pkill -f -- '-m pymta\.server' || true
    ;;
status)
    if smtp_ready; then echo "pymta: up"; else echo "pymta: down"; fi
    ;;
*)
    echo "usage: pymtactl [start|stop|status]" >&2
    exit 2
    ;;
esac
EOF

# The same Node image as src/frontend/Dockerfile. A bookworm binary runs on
# trixie. engines.node of package.json names the same version.
COPY --from=node:24.18.0-slim@sha256:cb4e8f7c443347358b7875e717c29e27bf9befc8f5a26cf18af3c3dec80e58c5 /usr/local/bin/node /usr/local/bin/node
COPY --from=node:24.18.0-slim@sha256:cb4e8f7c443347358b7875e717c29e27bf9befc8f5a26cf18af3c3dec80e58c5 /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN <<EOR
set -eux
ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm
ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
node --version
npm --version
# The same release as the mailcatcher service of compose.yaml.
npm install -g --no-audit --no-fund maildev@2.2.1
maildev --help >/dev/null
EOR

# uv and the managed CPython, as in deploy/python-uv/Dockerfile.
COPY --from=ghcr.io/astral-sh/uv@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON_PREFERENCE=only-managed

RUN uv python install 3.14.7

# Each component keeps its own virtual environment, because the three
# pyproject.toml files pin different versions of the same packages. The
# environments sit outside /src, so a search of the checkout does not read them.
#
# No cache mount: a cache mount is not kept in the image, and the agent needs
# the download caches to install anything with no network.
COPY src/backend/pyproject.toml src/backend/uv.lock /src/src/backend/
RUN cd /src/src/backend \
    && UV_PROJECT_ENVIRONMENT=/opt/venv/backend \
       uv sync --frozen --no-install-project --no-editable --all-extras

COPY src/mta-in/pyproject.toml src/mta-in/uv.lock /src/src/mta-in/
RUN cd /src/src/mta-in \
    && UV_PROJECT_ENVIRONMENT=/opt/venv/mta-in \
       uv sync --frozen --no-install-project --no-editable --all-extras

COPY src/jmap-email/pyproject.toml src/jmap-email/uv.lock /src/src/jmap-email/
RUN cd /src/src/jmap-email \
    && UV_PROJECT_ENVIRONMENT=/opt/venv/jmap-email \
       uv sync --frozen --no-install-project --all-extras

# .npmrc selects the symlinked install layout the project needs. That layout
# opens a very large number of files, so the build takes the highest limit it
# is allowed, or npm stops with EMFILE.
COPY src/frontend/package.json src/frontend/package-lock.json src/frontend/.npmrc /src/src/frontend/
RUN <<EOR
set -eux
ulimit -n "$(ulimit -Hn)" || true
cd /src/src/frontend
npm ci --no-audit --no-fund
EOR

COPY . /src
WORKDIR /src

# PYTHONPATH lets a script outside src/backend import the project, the way
# the reference images of the project run from /app.
ENV PATH=/opt/venv/backend/bin:$PATH \
    VIRTUAL_ENV=/opt/venv/backend \
    PYTHONPATH=/src/src/backend

# The backend reads the parser from the checkout, the way compose.yaml mounts
# src/jmap-email over the wheel the lock file pins. The scanner then reports on
# the parser that the backend tests exercise. The mta-in images put src/ on
# PYTHONPATH for the same reason; the .pth file does it for that environment.
RUN <<EOR
set -eux
cd /src/src/jmap-email
UV_PROJECT_ENVIRONMENT=/opt/venv/jmap-email uv sync --frozen --all-extras
uv pip install --python /opt/venv/backend/bin/python --no-deps --editable /src/src/jmap-email
site="$(/opt/venv/mta-in/bin/python -c 'import site; print(site.getsitepackages()[0])')"
echo /src/src/mta-in/src > "$site/_mta_in.pth"
/opt/venv/backend/bin/python -c \
    'import jmap_email; assert jmap_email.__file__.startswith("/src/"), jmap_email.__file__'
/opt/venv/mta-in/bin/python -c 'import pymta, delivery_milter'
/opt/venv/jmap-email/bin/python -c 'import jmap_email'
EOR

# A throwaway certificate, so tests/test_smtp_protocol.py can exercise STARTTLS.
# The runtime-dev stage of src/mta-in/Dockerfile.pymta builds the same thing.
RUN <<EOR
set -eux
mkdir -p /opt/pymta-certs
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -subj "/CN=mta-in.test" \
    -keyout /opt/pymta-certs/pymta-test.key \
    -out /opt/pymta-certs/pymta-test.crt
chmod 600 /opt/pymta-certs/pymta-test.key
EOR

# The Test configuration of src/backend/messages/settings.py, with every service
# on the loopback address. The values mirror deploy/env/backend.defaults: they
# are the public development values, not secrets. The OIDC endpoints are never
# called, but RS256 refuses to load without a JWKS endpoint. OPENSEARCH_URL is
# empty on purpose: the full-text search tests skip themselves when the host
# list is empty. The MTA_* and MDA_API_BASE_URL values are those of the
# mta-in-py-test service of compose.yaml.
ENV DJANGO_SETTINGS_MODULE=messages.settings \
    DJANGO_CONFIGURATION=Test \
    DJANGO_SECRET_KEY=ThisIsAnExampleKeyForDevPurposeOnly \
    DJANGO_ALLOWED_HOSTS=* \
    DJANGO_DATA_DIR=/data \
    DB_HOST=127.0.0.1 \
    DB_PORT=5432 \
    DB_NAME=messages \
    DB_USER=user \
    DB_PASSWORD=pass \
    REDIS_URL=redis://127.0.0.1:6379 \
    CELERY_BROKER_URL=redis://127.0.0.1:6379 \
    OPENSEARCH_URL= \
    AWS_S3_ENDPOINT_URL=http://127.0.0.1:9000 \
    AWS_S3_ACCESS_KEY_ID=st-messages \
    AWS_S3_SECRET_ACCESS_KEY=password \
    AWS_S3_SIGNATURE_VERSION=s3v4 \
    AWS_STORAGE_BUCKET_NAME=st-messages-media-storage \
    STORAGE_MESSAGE_IMPORTS_ENDPOINT_URL=http://127.0.0.1:9000 \
    STORAGE_MESSAGE_IMPORTS_BUCKET_NAME=msg-imports \
    STORAGE_MESSAGE_IMPORTS_ACCESS_KEY=st-messages \
    STORAGE_MESSAGE_IMPORTS_SECRET_KEY=password \
    STORAGE_MESSAGE_IMPORTS_EXPIRE_POLICY=600 \
    STORAGE_MESSAGE_BLOBS_ENDPOINT_URL=http://127.0.0.1:9000 \
    STORAGE_MESSAGE_BLOBS_BUCKET_NAME=msg-blobs \
    STORAGE_MESSAGE_BLOBS_ACCESS_KEY=st-messages \
    STORAGE_MESSAGE_BLOBS_SECRET_KEY=password \
    MDA_API_SECRET=my-shared-secret-mda \
    SALT_KEY=ThisIsAnExampleSaltForDevPurposeOnly \
    OIDC_OP_JWKS_ENDPOINT=http://keycloak:8802/realms/messages/protocol/openid-connect/certs \
    OIDC_OP_AUTHORIZATION_ENDPOINT=http://localhost:8902/realms/messages/protocol/openid-connect/auth \
    OIDC_OP_TOKEN_ENDPOINT=http://keycloak:8802/realms/messages/protocol/openid-connect/token \
    OIDC_OP_USER_ENDPOINT=http://keycloak:8802/realms/messages/protocol/openid-connect/userinfo \
    OIDC_OP_LOGOUT_ENDPOINT=http://localhost:8902/realms/messages/protocol/openid-connect/logout \
    OIDC_RP_CLIENT_ID=messages \
    OIDC_RP_CLIENT_SECRET=ThisIsAnExampleKeyForDevPurposeOnly \
    OIDC_RP_SIGN_ALGO=RS256 \
    OIDC_RP_SCOPES="openid email" \
    OIDC_REDIRECT_ALLOWED_HOSTS=localhost:8902,localhost:8900 \
    OIDC_AUTH_REQUEST_EXTRA_PARAMS='{"acr_values": "eidas1"}' \
    LOGIN_REDIRECT_URL=http://localhost:8900 \
    LOGIN_REDIRECT_URL_FAILURE=http://localhost:8900 \
    LOGOUT_REDIRECT_URL=http://localhost:8900 \
    MDA_API_BASE_URL=http://localhost:8000/api/mail/ \
    MDA_API_TIMEOUT=2 \
    MTA_HOST=localhost \
    MTA_PORT=25 \
    MTA_IMPL=pymta \
    MTA_METRICS_URL=http://localhost:9100/metrics \
    PYMTA_TLS_CERT_FILE=/opt/pymta-certs/pymta-test.crt \
    PYMTA_TLS_KEY_FILE=/opt/pymta-certs/pymta-test.key

# "user" needs the quotes: it is a reserved word in SQL. The role is a
# superuser because core/migrations/0001 creates the unaccent extension and
# pytest-django creates the test databases.
RUN <<EOR
set -eux
install -d -m 0777 /data /data/static /data/media
svcctl start
test "$(runuser -u postgres -- psql -tAXc 'show server_encoding')" = UTF8
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -X \
    -c "CREATE ROLE \"${DB_USER}\" LOGIN SUPERUSER CREATEDB PASSWORD '${DB_PASSWORD}';"
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -X \
    -c "CREATE DATABASE \"${DB_NAME}\" OWNER \"${DB_USER}\";"
cd /src/src/backend
python manage.py migrate --noinput
python manage.py collectstatic --noinput
svcctl stop
EOR

# A failing test must not fail the build, and each run has a time limit because
# a test that waits on a socket would spend the whole 45 minutes. The scanner
# keeps a tenth of that for its own layers. The limits add up to 31 minutes,
# which leaves time for the installation steps. -k kills a worker that ignores
# the first signal.
RUN <<EOR
set -eux
cd /src/src/jmap-email
timeout -k 30 240 /opt/venv/jmap-email/bin/pytest -q -p no:cacheprovider --no-cov \
    || echo "WARNING: the jmap-email test run did not finish cleanly (exit $?)"
EOR

RUN <<EOR
set -eux
pymtactl start
cd /src/src/mta-in
timeout -k 30 240 /opt/venv/mta-in/bin/pytest -q -p no:cacheprovider --no-cov tests \
    || echo "WARNING: the mta-in test run did not finish cleanly (exit $?)"
pymtactl stop
EOR

# --reuse-db keeps the migrated test databases in the image: one per xdist
# worker, and the serial one that the second run creates for the agent's first
# `pytest --reuse-db`.
RUN <<EOR
set -eux
svcctl start
cd /src/src/backend
timeout -k 30 720 pytest --reuse-db -q -p no:cacheprovider --no-cov -n auto \
    || echo "WARNING: the parallel test run did not finish cleanly (exit $?)"
timeout -k 30 240 pytest --reuse-db -q -p no:cacheprovider --no-cov core/tests/models \
    || echo "WARNING: the serial test run did not finish cleanly (exit $?)"
svcctl stop
EOR

RUN <<EOR
set -eux
cd /src/src/frontend
timeout -k 30 420 npm run test \
    || echo "WARNING: the frontend test run did not finish cleanly (exit $?)"
EOR

RUN <<EOR
set -eux
echo '. /etc/oss-scanner-shell-init' > /etc/profile.d/oss-scanner.sh
echo '. /etc/oss-scanner-shell-init' >> /root/.bashrc
git config --system --add safe.directory /src
python -c 'import socket; socket.gethostbyname(socket.gethostname())'
EOR

# From here on every tool reads the caches in this image and fails fast
# instead of waiting for a network that is not there.
ENV BASH_ENV=/etc/oss-scanner-shell-init \
    NO_UPDATE_NOTIFIER=1 \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_OFFLINE=true \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    PIP_NO_INDEX=1 \
    UV_FROZEN=1 \
    UV_NO_SYNC=1 \
    UV_OFFLINE=1
