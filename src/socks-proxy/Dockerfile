FROM debian:trixie-slim AS base

ARG DANTE_VER=1.4.4
ARG DANTE_URL=https://www.inet.no/dante/files/dante-$DANTE_VER.tar.gz
ARG DANTE_SHA256=1973c7732f1f9f0a4c0ccf2c1ce462c7c25060b25643ea90f9b98f53a813faec

RUN <<EOR
apt-get update
DEBIAN_FRONTEND="noninteractive" apt-get install -y build-essential curl
EOR

RUN <<EOR
set -eu
curl -fsSL -o dante.tar.gz $DANTE_URL
echo "$DANTE_SHA256 dante.tar.gz" | sha256sum -c -
tar -xzf dante.tar.gz
cd dante-$DANTE_VER
./configure
make
make install
EOR

FROM debian:trixie-slim AS runtime

COPY --from=base /usr/local/sbin/sockd /usr/local/sbin/sockd
COPY --chmod=0755 entrypoint.sh /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
HEALTHCHECK --interval=30s --timeout=3s --start-interval=5s \
  CMD cat /proc/$(cat /var/run/sockd.pid)/status | grep State | grep -E 'R \(running\)|S \(sleeping\)'
CMD ["/usr/local/sbin/sockd"]
