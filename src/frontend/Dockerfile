FROM node:22-alpine AS frontend-deps

ENV npm_config_cache=/tmp/npm-cache
RUN npm install -g npm@11.6.2 && npm cache clean -f

ARG DOCKER_USER
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
