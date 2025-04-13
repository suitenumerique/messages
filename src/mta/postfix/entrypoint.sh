#!/bin/bash

set -e
echo "Configuring Postfix..."

# Postfix configuration from environment variables
echo >> /etc/postfix/main.cf
echo "message_size_limit=${MESSAGE_SIZE_LIMIT:-10240000}" >> /etc/postfix/main.cf

# postconf |grep limit

# Dump env vars to files in /etc/st-messages/env/
mkdir -p /etc/st-messages/env/
echo -n "$MDA_API_URL" > /etc/st-messages/env/MDA_API_URL
echo -n "$REGIE_API_URL" > /etc/st-messages/env/REGIE_API_URL
echo -n "$MDA_API_SECRET" > /etc/st-messages/env/MDA_API_SECRET

# Here we can set some configuration based on environment variables

echo "Starting Postfix..."
exec postfix start-fg