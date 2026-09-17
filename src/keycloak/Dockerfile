FROM alpine:3 AS scripts

ARG SCRIPT_GROUP_ATTRIBUTE_WHITELIST

COPY ./scripts /scripts

RUN <<EOR
set -e
apk --no-cache add zip
cd /scripts
if [ -n "$SCRIPT_GROUP_ATTRIBUTE_WHITELIST" ]; then
    sed -i "s/SCRIPT_GROUP_ATTRIBUTE_WHITELIST/$SCRIPT_GROUP_ATTRIBUTE_WHITELIST/g" map-group-attribute.js
fi
zip -r /custom-scripts.jar META-INF *.js
EOR

FROM alpine:3.24@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b AS tools-download
ARG TARGETARCH

ARG CADDY_VERSION=2.11.4
# When upgrading, run
# for arch in amd64 arm64; do printf "CADDY_SHA256_%s=%s\n" "$(echo $arch | tr a-z A-Z)" "$(wget -qO- "https://github.com/caddyserver/caddy/releases/download/v2.11.4/caddy_2.11.4_linux_${arch}.tar.gz" | sha256sum | cut -d' ' -f1)"; done
# and update checksums accordingly
ARG CADDY_SHA256_AMD64=527fbf917c39189a1e3b31d34fa955601680b2d5c8055d2a87b8b9588dec7bb9
ARG CADDY_SHA256_ARM64=52d42ae12b3462097e9868da6dfed3c9648ae12edd3b3638102312af84cb6904
RUN wget -qO /tmp/caddy.tar.gz "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_${TARGETARCH}.tar.gz" \
    && case "${TARGETARCH}" in \
         amd64) echo "${CADDY_SHA256_AMD64}  /tmp/caddy.tar.gz" | sha256sum -c - ;; \
         arm64) echo "${CADDY_SHA256_ARM64}  /tmp/caddy.tar.gz" | sha256sum -c - ;; \
         *) echo "Unsupported architecture: ${TARGETARCH}" && exit 1 ;; \
       esac \
    && tar -xzf /tmp/caddy.tar.gz caddy \
    && mv caddy /usr/bin/caddy \
    && rm /tmp/caddy.tar.gz

FROM quay.io/keycloak/keycloak:26.7.4@sha256:82a77884f3af238beab1e7afd63b5f530e1b5c0590bd7aa60b40a40463e29b2c AS builder

WORKDIR /opt/keycloak
COPY --chown=keycloak:keycloak --chmod=644 themes/dsfr-2.3.4.jar /opt/keycloak/providers/dsfr.jar
COPY --from=scripts --chown=keycloak:keycloak --chmod=644 /custom-scripts.jar /opt/keycloak/providers/custom-scripts.jar
# Committed JAR. Rebuild via `make build-keycloak` if you edit the Java
# source under bulk-role-membership/.
COPY --chown=keycloak:keycloak --chmod=644 bulk-role-membership/bulk-role-membership.jar /opt/keycloak/providers/bulk-role-membership.jar

ENV KC_FEATURES=scripts
ENV KC_FEATURES_DISABLED=kerberos
ENV KC_METRICS_ENABLED=true
ENV KC_EVENT_METRICS_USER_ENABLED=true
ENV KC_HEALTH_ENABLED=true
ENV KC_DB=postgres

RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:26.7.4@sha256:82a77884f3af238beab1e7afd63b5f530e1b5c0590bd7aa60b40a40463e29b2c

COPY --from=builder /opt/keycloak/ /opt/keycloak/
COPY --from=tools-download /usr/bin/caddy /usr/bin/caddy
COPY caddy/Caddyfile /etc/caddy/Caddyfile
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

# Same build options as the builder stage, so that a `start` without
# --optimized keeps them after a re-augmentation.
ENV KC_FEATURES=scripts
ENV KC_FEATURES_DISABLED=kerberos
ENV KC_METRICS_ENABLED=true
ENV KC_EVENT_METRICS_USER_ENABLED=true
ENV KC_HEALTH_ENABLED=true
ENV KC_DB=postgres

# Keycloak listens on loopback only; Caddy owns the public port. The
# management interface stays on all interfaces for the probes on 9000.
# The XDG dirs keep Caddy's autosave out of /opt/keycloak.
ENV PORT=8080
ENV KC_HTTP_HOST=127.0.0.1
ENV KC_HTTP_PORT=8081
ENV KC_HTTP_ENABLED=true
ENV KC_HTTP_MANAGEMENT_HOST=0.0.0.0
ENV KC_PROXY_HEADERS=xforwarded
ENV KC_PROXY_TRUSTED_ADDRESSES=127.0.0.1
ENV XDG_DATA_HOME=/tmp/caddy
ENV XDG_CONFIG_HOME=/tmp/caddy

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# https://www.keycloak.org/observability/health#_healthcheck
# Shell form: the ports come from the container ENV at run time.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD { printf 'HEAD /__lbheartbeat__ HTTP/1.0\r\n\r\n' >&0; grep 'HTTP/1.0 200'; } 0<>/dev/tcp/127.0.0.1/${PORT:-8080} && { printf 'HEAD /health/ready HTTP/1.0\r\n\r\n' >&0; grep 'HTTP/1.0 200'; } 0<>/dev/tcp/127.0.0.1/${KC_HTTP_MANAGEMENT_PORT:-9000}
