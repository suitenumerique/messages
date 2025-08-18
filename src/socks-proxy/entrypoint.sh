#!/bin/bash
set -euo pipefail

if [ -z "${PROXY_USERS:-}" ]; then
  echo "Error: PROXY_USERS env var is not set (format: user1:pass1,user2:pass2)"
  exit 1
fi

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

# Replace the placeholder with multiple client pass and pass sections for each IP range
PROXY_SOURCE_IP_WHITELIST=${PROXY_SOURCE_IP_WHITELIST:-"0.0.0.0/0"}

# Create the complete configuration sections for each IP range
DANTE_CONFIG=""
IFS=',' read -ra IP_RANGES <<< "$PROXY_SOURCE_IP_WHITELIST"
for ip_range in "${IP_RANGES[@]}"; do
    # Trim whitespace
    ip_range=$(echo "$ip_range" | xargs)
    
    DANTE_CONFIG+="

# Allow all clients, but require auth
client pass {
  from: $ip_range
  to: 0.0.0.0/0
  log: connect error
}

# Pass authenticated users
pass {
  from: $ip_range
  to: 0.0.0.0/0
  protocol: tcp
  method: username
  command: bind connect
  log: connect error
}

"
done

# Replace the placeholder with the generated configuration
echo "$DANTE_CONFIG" >> /etc/sockd.conf

# env var for debug level, default to 1
DEBUG_LEVEL=${DEBUG_LEVEL:-1}

exec /usr/local/sbin/sockd -d "$DEBUG_LEVEL"