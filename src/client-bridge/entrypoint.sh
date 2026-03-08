#!/bin/bash

set -e

if [ "${EXEC_CMD_ONLY:-false}" = "true" ]; then
    exec "$@"
fi

echo "Starting client-bridge server..."

# If env var EXEC_CMD is true, run the tests or another command
if [ "${EXEC_CMD:-false}" = "true" ]; then
    "$@"
    exit $?
fi

exec python -m src.server
