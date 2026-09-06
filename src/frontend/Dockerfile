ARG FRONTEND_IMAGE=frontend-build-output

FROM node:24.18.0-slim@sha256:cb4e8f7c443347358b7875e717c29e27bf9befc8f5a26cf18af3c3dec80e58c5 AS frontend-dev-base

ENV npm_config_cache=/tmp/npm-cache

ARG DOCKER_USER=1000
WORKDIR /home/frontend/

# Pre-create an empty node_modules owned by DOCKER_USER. The runtime mounts a
# named volume there (see compose.yaml); Docker initializes an empty volume from
# the image's directory *including its ownership*, so this is what makes the
# volume writable by the non-root container user. Without it the fresh volume is
# root-owned and `npm ci` fails with EACCES.
RUN mkdir -p /tmp/npm-cache /home/frontend/node_modules \
    && chown -R ${DOCKER_USER} /tmp/npm-cache /home/frontend

# Let a process running with an arbitrary uid (the host uid on bind mounts) and
# group root register itself in /etc/passwd at runtime. The Capacitor CLI calls
# os.userInfo(), which throws when the uid has no passwd entry (glibc and musl
# alike; see the frontend-mobile entrypoint in compose.yaml). Harmless otherwise.
RUN chmod g=u /etc/passwd

# Run all npm commands as the user running the container.
USER ${DOCKER_USER}

# ---- Production dependency install (ONLY for the prod build below) ----
# frontend-build runs `npm run build`, which needs node_modules present in the
# image; the dev/tools services use the lean frontend-dev-base above instead.
FROM frontend-dev-base AS frontend-deps
COPY --chown=${DOCKER_USER} package*.json .npmrc ./
RUN npm ci

FROM frontend-deps AS frontend-build

ARG DOCKER_USER=1000
WORKDIR /home/frontend/
COPY --chown=${DOCKER_USER} . ./

ARG API_ORIGIN
ENV NEXT_PUBLIC_API_ORIGIN=${API_ORIGIN}

# Commit SHA (or any per-deploy version) baked in as the asset cache-buster —
# see vite.config.ts. Named SOURCE_VERSION to match the var Scalingo's buildpack
# sets natively. Pass `--build-arg SOURCE_VERSION=$(git rev-parse --short HEAD)`;
# if omitted the build falls back to a timestamp (still unique per deploy).
ARG SOURCE_VERSION
ENV SOURCE_VERSION=${SOURCE_VERSION}

RUN npm run build

# Normalize output path to /app (matches the runtime-prod layout)
FROM scratch AS frontend-build-output
COPY --from=frontend-build /home/frontend/dist /app

# When FRONTEND_IMAGE is set to an external image, BuildKit skips
# frontend-deps, frontend-build, and frontend-build-output entirely
FROM ${FRONTEND_IMAGE} AS frontend-source

FROM alpine:3.21@sha256:48b0309ca019d89d40f670aa1bc06e426dc0931948452e8491e3d65087abc07d AS tools-download
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

ARG LPROBE_VERSION=v0.2.0
# When upgrading, run
# for arch in amd64 arm64; do printf "LPROBE_SHA256_%s=%s\n" "$(echo $arch | tr a-z A-Z)" "$(wget -qO- "https://github.com/fivexl/lprobe/releases/download/v0.2.0/lprobe-linux-${arch}" | sha256sum | cut -d' ' -f1)"; done
# and update checksums accordingly
ARG LPROBE_SHA256_AMD64=8b55ddb15c2d8c5e1773c73c1413b26c7041e5836dabdbcef5f806ba6b9d17a2
ARG LPROBE_SHA256_ARM64=3f31eee19167f23dc64c645021b75306956da245e5b9763c90abd12797d0a72a
RUN wget -qO /usr/bin/lprobe "https://github.com/fivexl/lprobe/releases/download/${LPROBE_VERSION}/lprobe-linux-${TARGETARCH}" \
    && case "${TARGETARCH}" in \
         amd64) echo "${LPROBE_SHA256_AMD64}  /usr/bin/lprobe" | sha256sum -c - ;; \
         arm64) echo "${LPROBE_SHA256_ARM64}  /usr/bin/lprobe" | sha256sum -c - ;; \
         *) echo "Unsupported architecture: ${TARGETARCH}" && exit 1 ;; \
       esac \
    && chmod +x /usr/bin/lprobe

FROM gcr.io/distroless/static-debian13:nonroot AS runtime-prod

COPY --from=tools-download /usr/bin/caddy /usr/bin/caddy
COPY --from=tools-download /usr/bin/lprobe /usr/bin/lprobe
COPY --from=frontend-source /app /app
COPY caddy/Caddyfile /etc/caddy/Caddyfile

ENV PORT=8080
ENV MESSAGES_FRONTEND_ROOT=/app
ENV MESSAGES_FRONTEND_BACKEND_SERVER=localhost:8000
ENV DJANGO_ADMIN_URL=admin

ENTRYPOINT ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]

# Port is hardcoded below because distroless has no shell to expand $PORT.
# If you change ENV PORT above, update the healthcheck port to match.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD ["/usr/bin/lprobe", "-mode=http", "-port=8080", "-endpoint=/__lbheartbeat__"]
