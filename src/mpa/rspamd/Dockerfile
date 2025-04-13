FROM debian:12.10-slim

RUN apt-get update && \
    apt-get install -y lsb-release wget gpg ruby nginx && \
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

# Copy nginx configuration files
COPY base_nginx.conf /etc/nginx/nginx.conf
COPY servers.conf.erb /etc/nginx/servers.conf.erb
COPY start.sh /start.sh

RUN chmod +x /start.sh

ENV PORT=8010

CMD ["/start.sh"]