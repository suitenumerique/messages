#!/bin/bash

set -e
echo "Configuring Postfix via Jinja2 template..."

# === Environment Variables & Defaults ===
# Set required vars (will exit if not set)
: "${SMTP_USERNAME:?Error: SMTP_USERNAME must be set}"
: "${SMTP_PASSWORD:?Error: SMTP_PASSWORD must be set}"

# Set optional vars with defaults
export MESSAGE_SIZE_LIMIT=${MESSAGE_SIZE_LIMIT:-10240000}
export RELAYHOST=${RELAYHOST:-""}
export SMTP_RELAY_USERNAME=${SMTP_RELAY_USERNAME:-""}
export SMTP_RELAY_PASSWORD=${SMTP_RELAY_PASSWORD:-""}
export TLS_CERT_PATH=${TLS_CERT_PATH:-/etc/ssl/certs/ssl-cert-snakeoil.pem}
export TLS_KEY_PATH=${TLS_KEY_PATH:-/etc/ssl/private/ssl-cert-snakeoil.key}

# Get the rDNS of this host if not set
# TODO: remove the unreliable external dependency on ifconfig.me
if [ -z "$MYHOSTNAME" ]; then
  export MYHOSTNAME=$(dig -x $(curl -s ifconfig.me) +short | tail -n1 | sed 's/\.$//')
  echo "Detected hostname from rDNS: $MYHOSTNAME"
fi

# === Validate TLS Files ===
if [ ! -f "$TLS_CERT_PATH" ] || [ ! -f "$TLS_KEY_PATH" ]; then
  echo "Error: TLS certificate/key files not found: $TLS_CERT_PATH, $TLS_KEY_PATH"
  exit 1
fi

# We use the sasldb2 file to store the password for the SMTP user, in the chroot jail.
echo "$SMTP_PASSWORD" | saslpasswd2 -p -c -f /var/spool/postfix/etc/sasldb2 -u "$MYHOSTNAME" "$SMTP_USERNAME"
chown root:postfix /var/spool/postfix/etc/sasldb2

# === Render main.cf from Template ===
echo "Rendering /app/main.cf.j2 to /etc/postfix/main.cf..."
# Pass all environment variables to the template context

python3 -c "
import os
import jinja2

template_path = '/app/main.cf.j2'
output_path = '/etc/postfix/main.cf'

# Use all environment variables as context
context = dict(os.environ)

template_dir = os.path.dirname(template_path)
loader = jinja2.FileSystemLoader(template_dir)
env = jinja2.Environment(loader=loader)
template = env.get_template(os.path.basename(template_path))

rendered_config = template.render(context)

with open(output_path, 'w') as f:
    f.write(rendered_config)

print(f'Successfully rendered {output_path}')
"

# === Configure Authentication TO Relay Host (if needed) ===
# (Password map file, separate from main.cf)
if [ -n "$RELAYHOST" ] && [ -n "$SMTP_RELAY_USERNAME" ] && [ -n "$SMTP_RELAY_PASSWORD" ]; then
  RELAY_PASSWD_FILE="/etc/postfix/sasl/relay_passwd"
  echo "Creating $RELAY_PASSWD_FILE for relay host authentication..."
  echo "$RELAYHOST $SMTP_RELAY_USERNAME:$SMTP_RELAY_PASSWORD" > "$RELAY_PASSWD_FILE"
  chmod 600 "$RELAY_PASSWD_FILE"
  # Create the Postfix lookup table database
  postmap "$RELAY_PASSWD_FILE"
  echo "Created $RELAY_PASSWD_FILE.db for relay host $RELAYHOST"
elif [ -n "$RELAYHOST" ] && ( [ -n "$SMTP_RELAY_USERNAME" ] || [ -n "$SMTP_RELAY_PASSWORD" ] ); then
  # Warn if only one of username/password is provided for relay
  echo "Warning: SMTP_RELAY_USERNAME or SMTP_RELAY_PASSWORD provided without the other for RELAYHOST=$RELAYHOST. Relay authentication disabled." >&2
fi

# === Final Steps ===
echo "Verifying Postfix configuration (/etc/postfix/main.cf)..."

postfix check -v || exit 1

# cat /etc/postfix/main.cf

# Start Postfix in the foreground (standard way)
postfix start-fg -v
