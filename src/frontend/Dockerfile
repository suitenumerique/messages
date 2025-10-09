FROM node:22-slim AS frontend-deps

WORKDIR /home/frontend/

RUN npm install -g npm@11.6.2 && npm cache clean -f

ARG DOCKER_USER
USER ${DOCKER_USER}

ENV npm_config_cache=/tmp/npm-cache
