FROM node:22-alpine AS frontend-deps

ENV npm_config_cache=/tmp/npm-cache
RUN npm install -g npm@11.6.2 && npm cache clean -f

ARG DOCKER_USER=1000
WORKDIR /home/frontend/

RUN chown -R ${DOCKER_USER} /tmp/npm-cache
RUN chown -R ${DOCKER_USER} /home/frontend

# Install deps and run all npm commands as the user running the container
USER ${DOCKER_USER}
COPY --chown=${DOCKER_USER} package*.json ./
RUN npm install

FROM frontend-deps AS frontend-build

WORKDIR /home/frontend/
COPY . ./

ARG API_ORIGIN
ENV NEXT_PUBLIC_API_ORIGIN=${API_ORIGIN}

RUN npm run build

FROM alpine:3.21 AS caddy-download
ARG TARGETARCH
ARG CADDY_VERSION=2.9.1
RUN wget -qO- "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_${TARGETARCH}.tar.gz" | tar -xz caddy

FROM gcr.io/distroless/static-debian13:nonroot AS runtime-prod

COPY --from=caddy-download /caddy /usr/bin/caddy
COPY --from=frontend-build /home/frontend/out /app
COPY caddy/Caddyfile /etc/caddy/Caddyfile

ENV PORT=8080
ENV MESSAGES_FRONTEND_ROOT=/app
ENV MESSAGES_FRONTEND_BACKEND_SERVER=localhost:8000
ENV DJANGO_ADMIN_URL=admin

ENTRYPOINT ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
