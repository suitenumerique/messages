#!/bin/bash
# Caddy on :$PORT sits in front of Keycloak on 127.0.0.1:$KC_HTTP_PORT.
# The first process to exit ends the container.
set -u

supervise=false
for arg in "$@"; do
  case "$arg" in
    start|start-dev) supervise=true ;;
  esac
done
if [ "$supervise" = false ]; then
  exec /opt/keycloak/bin/kc.sh "$@"
fi

caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &
caddy_pid=$!
/opt/keycloak/bin/kc.sh "$@" &
kc_pid=$!

trap 'kill -TERM "$caddy_pid" "$kc_pid" 2>/dev/null' TERM INT

wait -n; status=$?
trap - TERM INT
kill -TERM "$caddy_pid" "$kc_pid" 2>/dev/null
wait
exit "$status"
