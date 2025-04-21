FROM debian:12.10-slim

WORKDIR /app

# Install Postfix, SASL libs (for Postfix integration), Python, Jinja2, and necessary tools
RUN apt-get update && apt-get install -y --no-install-recommends \
  ca-certificates \
  postfix \
  sasl2-bin \
  libsasl2-modules \
  ssl-cert \
  procps \
  python3 \
  python3-jinja2 \
  curl \
  dnsutils \
  && rm -rf /var/lib/apt/lists/*

# Create directories for Postfix queues and SASL socket area
RUN mkdir -p /etc/postfix/sasl /var/spool/postfix/private /var/spool/postfix/public

# Create directories for Postfix queues
RUN mkdir -p /var/spool/postfix/private /var/spool/postfix/public

# Ensure self-signed certificates are available (can be overridden by env vars/mounts)
RUN make-ssl-cert generate-default-snakeoil

# Copy Postfix base configuration (master.cf and the template)
COPY ./etc/master.cf /etc/postfix/
COPY ./etc/main.cf.j2 /app/main.cf.j2
COPY ./etc/header_checks /etc/postfix/header_checks
COPY ./etc/sasl/smtpd.conf /etc/postfix/sasl/smtpd.conf

# We don't need the default sasldb2 file, we use the one in the chroot jail.
RUN rm -rf /etc/sasldb2

# Copy entrypoint script
COPY ./entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 587

ENTRYPOINT ["/entrypoint.sh"] 