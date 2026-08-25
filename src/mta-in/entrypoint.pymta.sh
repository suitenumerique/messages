#!/bin/sh
# Entrypoint for the pure-Python (aiosmtpd) inbound MTA image.
#
# Two modes:
#  - EXEC_CMD_ONLY=true: skip starting the server, exec the user command. Used
#    by ad-hoc tooling like `ruff format .`.
#  - EXEC_CMD=true: start the server in the background, then exec the user
#    command. Used by the test runner to colocate pytest + pymta in a single
#    container (mirroring the postfix mta-in-test workflow).
#  - default: exec the server in the foreground.
set -eu

if [ "${EXEC_CMD_ONLY:-false}" = "true" ]; then
    exec "$@"
fi

start_pymta() {
    python -m pymta.server &
    PYMTA_PID=$!
    # Wait until the SMTP port accepts connections (max ~15s). Uses stdlib
    # socket rather than nc so the runtime image doesn't need netcat just
    # for this probe.
    port="${PYMTA_SMTP_BIND_PORT:-25}"
    # Probe whatever the server was told to bind. 127.0.0.1 is only right for a
    # wildcard bind; against an explicit address it reports the process dead
    # while it is serving happily, which on an orchestrator means restarting a
    # working MTA.
    probe="import os,socket;h=os.getenv('PYMTA_SMTP_BIND_HOST','').strip() or '127.0.0.1';h='127.0.0.1' if h in ('0.0.0.0','::') else h;socket.create_connection((h,int(os.getenv('PYMTA_SMTP_BIND_PORT','25'))),timeout=1).close()"
    for i in $(seq 1 30); do
        if python -c "$probe" 2>/dev/null; then
            echo "pymta SMTP ready on port $port"
            return 0
        fi
        sleep 0.5
    done
    echo "ERROR: pymta SMTP did not open port $port within 15s" >&2
    kill "$PYMTA_PID" 2>/dev/null || true
    return 1
}

cleanup() {
    if [ -n "${PYMTA_PID:-}" ]; then
        kill "$PYMTA_PID" 2>/dev/null || true
        # Give pymta a chance to flush logs / drain sessions before we exit.
        wait "$PYMTA_PID" 2>/dev/null || true
    fi
}
trap cleanup INT TERM

if [ "${EXEC_CMD:-false}" = "true" ]; then
    start_pymta
    status=$?
    if [ "$status" -ne 0 ]; then
        echo "ERROR: pymta failed to start, not executing command" >&2
        cleanup
        exit "$status"
    fi
    "$@"
    status=$?
    cleanup
    exit $status
fi

exec python -m pymta.server
