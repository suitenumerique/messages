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

echo "Verifying Postfix configuration..."
#postconf -M  # Print active services
#postconf -m  # Print supported map types

# Initialize postfix
postfix check -v || exit 1

echo "Starting Postfix..."
exec /usr/lib/postfix/sbin/master -c /etc/postfix -d