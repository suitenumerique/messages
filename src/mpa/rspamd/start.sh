#!/bin/sh

erb /etc/nginx/servers.conf.erb > /etc/nginx/servers.conf

# Start rspamd in background
rspamd -f -u _rspamd -g _rspamd -c /app/rspamd.conf &
# Start nginx in foreground
nginx