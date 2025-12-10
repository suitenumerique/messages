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

FROM docker.io/nginxinc/nginx-unprivileged:1-alpine AS runtime-prod

USER root
RUN apk --no-cache upgrade

USER nginx
COPY --from=frontend-build /home/frontend/out /app
COPY nginx/nginx.conf.template /etc/nginx/templates/default.conf.template

ENV PORT=8080
ENV MESSAGES_FRONTEND_ROOT=/app
ENV MESSAGES_FRONTEND_BACKEND_SERVER=localhost:8000
ENV DJANGO_ADMIN_URL=admin

HEALTHCHECK --interval=60s --timeout=3s --start-interval=10s \
  CMD curl -fsS http://localhost:${PORT}/__lbheartbeat__
