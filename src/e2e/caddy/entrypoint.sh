#!/bin/sh
set -e
if [ "$E2E_PROFILE" = "dev" ]; then
    export FRONTEND_SERVICE_NAME="frontend-dev"
else
    export FRONTEND_SERVICE_NAME="frontend"
fi
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
