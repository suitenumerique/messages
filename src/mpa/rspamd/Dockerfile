FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a

# Bump to force an apt refresh + security upgrade of the base OS.
ENV MIN_UPDATE_DATE="2026-10-05"

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y lsb-release wget gpg ruby nginx netcat-openbsd procps && \
    rm -rf /var/lib/apt/lists/*
RUN mkdir -p /etc/apt/keyrings
RUN wget -O- https://rspamd.com/apt-stable/gpg.key | gpg --dearmor | tee /etc/apt/keyrings/rspamd.gpg > /dev/null
RUN echo "deb [signed-by=/etc/apt/keyrings/rspamd.gpg] http://rspamd.com/apt-stable/ `lsb_release -c -s` main" | tee /etc/apt/sources.list.d/rspamd.list
RUN echo "deb-src [signed-by=/etc/apt/keyrings/rspamd.gpg] http://rspamd.com/apt-stable/ `lsb_release -c -s` main"  | tee -a /etc/apt/sources.list.d/rspamd.list
RUN apt-get update && \ 
    apt-get --no-install-recommends -y install rspamd && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/lib/rspamd && \
    mkdir -p /var/run/rspamd && \
    chown -R _rspamd:_rspamd /var/lib/rspamd /var/run/rspamd

# Set up nginx directories
RUN mkdir -p /usr/share/nginx/logs/ && \
    ln -sf /dev/stdout /usr/share/nginx/logs/access.log && \
    ln -sf /dev/stderr /usr/share/nginx/logs/error.log

# Copy configuration files
COPY rspamd.conf /app/rspamd.conf
COPY base_nginx.conf /etc/nginx/nginx.conf
COPY servers.conf.erb /etc/nginx/servers.conf.erb
COPY start.sh /start.sh

RUN chmod +x /start.sh

ENV PORT=8010

CMD ["/start.sh"]