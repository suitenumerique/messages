#!/bin/bash
# Helper script to run Django management commands in the backend container.
# Through the docker sock proxy service, it is possible to run commands in the
# backend container from within the e2e-runner container.
#
# Usage: ./backend-manage.sh <command> [args...]
# Examples:
#   ./backend-manage.sh flush --noinput
#   ./backend-manage.sh drop_all_tables
#   ./backend-manage.sh migrate
#   ./backend-manage.sh createsuperuser

set -e

# Run the Django management command in the backend container
docker compose -f /app/compose.yaml -p st-messages-e2e \
    exec -T backend python manage.py "$@"

