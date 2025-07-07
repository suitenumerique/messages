#!/bin/bash

set -e
echo "Configuring Postfix..."

# Postfix configuration from environment variables
echo >> /etc/postfix/main.cf
echo "message_size_limit=${MESSAGE_SIZE_LIMIT:-10240000}" >> /etc/postfix/main.cf

if [ "${ENABLE_PROXY_PROTOCOL:-false}" = "haproxy" ]; then
  echo "postscreen_upstream_proxy_protocol = haproxy" >> /etc/postfix/main.cf
fi

# Dump env vars to files in /etc/st-messages/env/
# They will be used by the Python scripts.
mkdir -p /etc/st-messages/env/
echo -n "$MDA_API_BASE_URL" > /etc/st-messages/env/MDA_API_BASE_URL
echo -n "$MDA_API_SECRET" > /etc/st-messages/env/MDA_API_SECRET
echo -n "$MDA_API_TIMEOUT" > /etc/st-messages/env/MDA_API_TIMEOUT

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

/venv/bin/python3 /app/scripts/delivery_milter.py &
MILTER_PID=$!

# Wait a moment for milter to start and create socket
sleep 3

# Ensure socket has proper permissions
if [ -S /var/spool/postfix/milter/delivery.sock ]; then
    chown postfix:postfix /var/spool/postfix/milter/delivery.sock
    chmod 660 /var/spool/postfix/milter/delivery.sock
    echo "Milter socket ready"
else
    echo "Warning: Milter socket not found"
fi

echo "Starting Postfix..."
/usr/lib/postfix/sbin/master -c /etc/postfix -d &
POSTFIX_PID=$!

# Function to cleanup and exit
cleanup() {
    echo "Shutting down..."
    kill $MILTER_PID 2>/dev/null || true
    kill $POSTFIX_PID 2>/dev/null || true
    exit 0
}

# Trap signals to cleanup properly
trap cleanup SIGTERM SIGINT

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
