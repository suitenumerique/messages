#!/bin/bash

set -e

if [ "${EXEC_CMD_ONLY:-false}" = "true" ]; then
    exec "$@"
fi

echo "Configuring Postfix..."

cp -r /app/etc/* /etc/postfix/

# Postfix configuration from environment variables
echo >> /etc/postfix/main.cf
echo "message_size_limit=${MESSAGE_SIZE_LIMIT:-10240000}" >> /etc/postfix/main.cf

if [ "${ENABLE_PROXY_PROTOCOL:-false}" = "haproxy" ]; then
  echo "postscreen_upstream_proxy_protocol = haproxy" >> /etc/postfix/main.cf
fi

echo "Verifying Postfix configuration..."
#postconf -M  # Print active services
#postconf -m  # Print supported map types

# Initialize postfix
postfix check -v || exit 1

echo "Starting delivery milter in background..."

# Create milter socket directory with proper permissions
mkdir -p /var/spool/postfix/milter
chown postfix:postfix /var/spool/postfix/milter
chmod 755 /var/spool/postfix/milter

/venv/bin/python3 /app/src/delivery_milter.py &
MILTER_PID=$!

# Wait until the milter is ready, with a maximum of 10 seconds
for i in {1..10}; do
    if [ -S /var/spool/postfix/milter/delivery.sock ]; then
        chown postfix:postfix /var/spool/postfix/milter/delivery.sock
        chmod 660 /var/spool/postfix/milter/delivery.sock
        echo "Milter socket ready"
        break
    fi
    sleep 1
done

# If socket still doesn't exist, exit
if [ ! -S /var/spool/postfix/milter/delivery.sock ]; then
    echo "ERROR: Milter socket not found"
    exit 1
fi

echo "Starting Postfix..."
/usr/lib/postfix/sbin/master -c /etc/postfix -d &
POSTFIX_PID=$!

# Function to cleanup and exit
cleanup() {
    echo "Shutting down..."
    kill $MILTER_PID 2>/dev/null || true
    kill $POSTFIX_PID 2>/dev/null || true
}

# Trap signals to cleanup properly
trap cleanup SIGTERM SIGINT

CMD_STATUS=0

# If env var EXEC_CMD is true, run the tests or another command
if [ "${EXEC_CMD:-false}" = "true" ]; then
    "$@"
    exit $?
fi

# Monitor both processes
while true; do
    # Check if milter process is still running
    if ! kill -0 $MILTER_PID 2>/dev/null; then
        echo "ERROR: Milter process died, exiting container"
        kill $POSTFIX_PID 2>/dev/null || true
        exit 1
    fi
    
    # Check if Postfix process is still running
    if ! kill -0 $POSTFIX_PID 2>/dev/null; then
        echo "ERROR: Postfix process died, exiting container"
        kill $MILTER_PID 2>/dev/null || true
        exit 1
    fi
    
    sleep 5
done

exit $CMD_STATUS