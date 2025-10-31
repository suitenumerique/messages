#!/usr/bin/env bash
set -euo pipefail
umask 0077

if [ -z "${PROXY_USERS:-}" ]; then
  echo "Error: PROXY_USERS env var is not set (format: user1:pass1,user2:pass2)"
  exit 1
fi

# Create unix users for each entry of PROXY_USERS
IFS=',' read -ra USERS <<< "$PROXY_USERS"
for entry in "${USERS[@]}"; do
  IFS=':' read -r user pass <<< "$entry"
  if id "$user" &>/dev/null; then
    echo "User $user already exists, skipping"
  else
    useradd -M -s /usr/sbin/nologin "$user"
  fi
  echo "$user:$pass" | chpasswd
done

# Create the complete configuration sections for each IP range
DANTE_CONFIG="
logoutput: stdout
errorlog: stderr
debug: ${PROXY_DEBUG_LEVEL:-0}

internal: ${PROXY_INTERNAL:-0.0.0.0} port = ${PROXY_INTERNAL_PORT:-1080}
external: ${PROXY_EXTERNAL:-eth0}

# Use password-file method
socksmethod: username
user.privileged: root
user.notprivileged: nobody
"

IFS=',' read -ra IP_RANGES <<< "${PROXY_SOURCE_IP_WHITELIST:-0.0.0.0/0}"
for ip_range in "${IP_RANGES[@]}"; do
    # Trim whitespace
    ip_range=$(echo "$ip_range" | xargs)

    DANTE_CONFIG+="

client pass {
  from: $ip_range
  to: 0.0.0.0/0
  log: connect error
}

socks pass {
  from: $ip_range
  to: 0.0.0.0/0
  protocol: tcp
  socksmethod: username
  command: connect
  log: connect error
}
"
done

# Replace the placeholder with the generated configuration
echo "$DANTE_CONFIG" > /etc/sockd.conf

exec "$@"
