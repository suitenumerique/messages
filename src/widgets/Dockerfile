FROM node:22-slim AS widgets-deps

WORKDIR /home/widgets/

RUN npm install -g npm@11.3.0 && npm cache clean -f

ARG DOCKER_USER
USER ${DOCKER_USER}

ENV npm_config_cache=/tmp/npm-cache
