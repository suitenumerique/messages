FROM node:22-alpine AS frontend-deps

WORKDIR /home/frontend/

COPY ./src/frontend/package.json ./package.json
COPY ./src/frontend/package-lock.json ./package-lock.json

RUN npm ci

COPY .dockerignore ./.dockerignore
# COPY ./src/frontend/.prettierrc.js ./.prettierrc.js
#COPY ./src/frontend/packages/eslint-config-messages ./packages/eslint-config-messages
COPY ./src/frontend ./apps/messages

### ---- Front-end builder image ----
FROM frontend-deps AS st-messages-dev

WORKDIR /home/frontend/apps/messages

ARG API_ORIGIN
ENV NEXT_PUBLIC_API_ORIGIN=${API_ORIGIN}

EXPOSE 3000

CMD [ "npm", "run", "dev"]

# # Tilt will rebuild messages target so, we dissociate messages and messages-builder 
# # to avoid rebuilding the app at every changes.
# FROM messages AS messages-builder

# WORKDIR /home/frontend/apps/messages

# ARG S3_DOMAIN_REPLACE
# ENV NEXT_PUBLIC_S3_DOMAIN_REPLACE=${S3_DOMAIN_REPLACE}

# RUN npm run build

# # ---- Front-end image ----
# FROM nginxinc/nginx-unprivileged:1.26-alpine AS frontend-production

# # Un-privileged user running the application
# ARG DOCKER_USER
# USER ${DOCKER_USER}

# COPY --from=messages-builder \
#     /home/frontend/apps/messages/out \
#     /usr/share/nginx/html

# COPY ./src/frontend/conf/default.conf /etc/nginx/conf.d
# COPY ./docker/files/usr/local/bin/entrypoint /usr/local/bin/entrypoint

# ENTRYPOINT [ "/usr/local/bin/entrypoint" ]

# CMD ["nginx", "-g", "daemon off;"]
