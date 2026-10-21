FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6 AS scripts

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

FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6 AS tools-download
ARG TARGETARCH

ARG CADDY_VERSION=2.11.7
# When upgrading, run
# for arch in amd64 arm64; do printf "CADDY_SHA256_%s=%s\n" "$(echo $arch | tr a-z A-Z)" "$(wget -qO- "https://github.com/caddyserver/caddy/releases/download/v2.11.7/caddy_2.11.7_linux_${arch}.tar.gz" | sha256sum | cut -d' ' -f1)"; done
# and update checksums accordingly
ARG CADDY_SHA256_AMD64=727b91701a392de6ebc5027509f548bf39979e5216340d0faed8fa5e69c84f8b
ARG CADDY_SHA256_ARM64=d8fc6d179a5d283028a472a5618564f6ad8a86fed513e64f032b3b0b7cc45e42
RUN wget -qO /tmp/caddy.tar.gz "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_${TARGETARCH}.tar.gz" \
    && case "${TARGETARCH}" in \
         amd64) echo "${CADDY_SHA256_AMD64}  /tmp/caddy.tar.gz" | sha256sum -c - ;; \
         arm64) echo "${CADDY_SHA256_ARM64}  /tmp/caddy.tar.gz" | sha256sum -c - ;; \
         *) echo "Unsupported architecture: ${TARGETARCH}" && exit 1 ;; \
       esac \
    && tar -xzf /tmp/caddy.tar.gz caddy \
    && mv caddy /usr/bin/caddy \
    && rm /tmp/caddy.tar.gz

# The theme jar is architecture independent, so the build runs on the native
# platform of the builder, also in the arm64 CI build.
FROM --platform=$BUILDPLATFORM node:24.18.0-slim@sha256:cb4e8f7c443347358b7875e717c29e27bf9befc8f5a26cf18af3c3dec80e58c5 AS theme-tools
RUN apt-get update \
    && apt-get install -y --no-install-recommends maven ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Keycloakify renames the jar from its cache dir into the project, so that cache
# must stay on the same filesystem: leave XDG_CACHE_HOME unset (node_modules/.cache).
# Only the npm cache and the Maven repository live on a separate cache mount.
RUN mkdir -p /cache && chmod 1777 /cache
ENV npm_config_cache=/cache/npm
ENV KEYCLOAKIFY_USE_DEFAULT_MAVEN_REPO=true
ENV MAVEN_OPTS=-Dmaven.repo.local=/cache/m2
ENV KEYCLOAKIFY_BUILD_FOR_KEYCLOAK_MAJOR_VERSION=26
WORKDIR /theme

FROM theme-tools AS theme-build
COPY theme/package.json theme/package-lock.json theme/.npmrc ./
RUN --mount=type=cache,target=/cache npm ci
COPY theme/ ./
RUN --mount=type=cache,target=/cache npm run build-keycloak-theme

FROM quay.io/keycloak/keycloak:26.8.1@sha256:79c623a22abacf3fe6c00a5fb30f5b7843d717b234033b80995891c06116d244 AS builder

WORKDIR /opt/keycloak
COPY --from=theme-build --chown=keycloak:keycloak --chmod=644 /theme/dist_keycloak/keycloak-theme-for-kc-all-other-versions.jar /opt/keycloak/providers/lst-dsfr.jar
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

FROM quay.io/keycloak/keycloak:26.8.1@sha256:79c623a22abacf3fe6c00a5fb30f5b7843d717b234033b80995891c06116d244

COPY --from=builder /opt/keycloak/ /opt/keycloak/
COPY --from=tools-download /usr/bin/caddy /usr/bin/caddy
COPY caddy/Caddyfile /etc/caddy/Caddyfile
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

# @TODO: drop once the UBI base ships a ca-certificates without this root,
# already removed from the Debian and Mozilla trust stores.
USER root
RUN keytool -delete -cacerts -storepass changeit -alias emsigneccrootca-g3
USER 1000

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
