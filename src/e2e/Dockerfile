# E2E tests Dockerfile

# Use docker CLI image to get docker binaries
FROM docker:27-cli AS docker-cli

# Main image
FROM node:24.18.0-slim@sha256:cb4e8f7c443347358b7875e717c29e27bf9befc8f5a26cf18af3c3dec80e58c5

ENV npm_config_cache=/tmp/npm-cache
ENV PLAYWRIGHT_BROWSERS_PATH=/tmp/playwright-browsers

ENV HOME=/tmp

# Copy docker and docker-compose binaries from docker-cli image
# Create the cli-plugins directory first
ARG DOCKER_USER
RUN mkdir -p /usr/local/libexec/docker/cli-plugins
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

WORKDIR /app


# Install deps and run all npm commands as the user running the container
COPY package*.json ./

# Install dependencies and postinstall playwright with deps
RUN npm install

# Copy test files
COPY . ./

# Fix ownership for the user (use numeric UID to avoid lookup issues)
RUN chown -R $DOCKER_USER /tmp/npm-cache /app

# Run test with unprivileged user
USER $DOCKER_USER
# Default command
CMD ["npm", "test"]

