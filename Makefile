# /!\ /!\ /!\ /!\ /!\ /!\ /!\ DISCLAIMER /!\ /!\ /!\ /!\ /!\ /!\ /!\ /!\
#
# This Makefile is only meant to be used for DEVELOPMENT purpose as we are
# changing the user id that will run in the container.
#
# PLEASE DO NOT USE IT FOR YOUR CI/PRODUCTION/WHATEVER...
#
# /!\ /!\ /!\ /!\ /!\ /!\ /!\ /!\ /!\ /!\ /!\ /!\ /!\ /!\ /!\ /!\ /!\ /!\
#
# Note to developers:
#
# While editing this file, please respect the following statements:
#
# 1. Every variable should be defined in the ad hoc VARIABLES section with a
#    relevant subsection
# 2. Every new rule should be defined in the ad hoc RULES section with a
#    relevant subsection depending on the targeted service
# 3. Rules should be sorted alphabetically within their section
# 4. When a rule has multiple dependencies, you should:
#    - duplicate the rule name to add the help string (if required)
#    - write one dependency per line to increase readability and diffs
# 5. .PHONY rule statement should be written after the corresponding rule
# ==============================================================================
# VARIABLES

BOLD := \033[1m
RESET := \033[0m
GREEN := \033[1;32m


# -- Database

DB_HOST            = postgresql
DB_PORT            = 5432

# -- Docker
# Get the current user ID to use for docker run and docker exec commands
DOCKER_UID          = $(shell id -u)
DOCKER_GID          = $(shell id -g)
DOCKER_USER         = $(DOCKER_UID):$(DOCKER_GID)
COMPOSE             = DOCKER_USER=$(DOCKER_USER) docker compose
COMPOSE_EXEC        = $(COMPOSE) exec
COMPOSE_EXEC_APP    = $(COMPOSE_EXEC) backend-dev
COMPOSE_RUN         = $(COMPOSE) run --rm
COMPOSE_RUN_APP     = $(COMPOSE_RUN) backend-dev
COMPOSE_RUN_APP_TOOLS = $(COMPOSE_RUN) --no-deps backend-dev
COMPOSE_RUN_CROWDIN = $(COMPOSE_RUN) crowdin crowdin
COMPOSE_RUN_MTA_IN_TESTS  = cd src/mta-in && $(COMPOSE_RUN) --build test
COMPOSE_RUN_MTA_OUT_TESTS = cd src/mta-out && $(COMPOSE_RUN) --build test

# -- Backend
MANAGE              = $(COMPOSE_RUN_APP) python manage.py


# ==============================================================================
# RULES

default: help

data/media:
	@mkdir -p data/media

data/static:
	@mkdir -p data/static

# -- Project

create-env-files: ## Copy the dist env files to env files
create-env-files: \
	env.d/development/common \
	env.d/development/crowdin \
	env.d/development/postgresql \
	env.d/development/kc_postgresql \
	env.d/development/backend \
	env.d/development/mta-in \
	env.d/development/mta-out
.PHONY: create-env-files

bootstrap: ## Prepare Docker images for the project
bootstrap: \
	data/media \
	data/static \
	create-env-files \
	build \
	migrate \
	back-i18n-compile \
	frontend-install-frozen
.PHONY: bootstrap

# -- Docker/compose
build: cache ?= --no-cache
build: ## build the project containers
	@$(MAKE) build-backend cache=$(cache)
	@$(MAKE) build-frontend-dev cache=$(cache)
.PHONY: build

build-backend: cache ?=
build-backend: ## build the backend-dev container
	@$(COMPOSE) build backend-dev $(cache)
.PHONY: build-backend

build-frontend-dev: cache ?=
build-frontend-dev: ## build the frontend container
	@$(COMPOSE) build frontend-dev $(cache)
.PHONY: build-frontend-dev

build-frontend: cache ?=
build-frontend: ## build the frontend container
	@$(COMPOSE) build frontend $(cache)
.PHONY: build-frontend

down: ## stop and remove containers, networks, images, and volumes
	@$(COMPOSE) down
.PHONY: down

logs: ## display backend-dev logs (follow mode)
	@$(COMPOSE) logs -f backend-dev
.PHONY: logs

build-run: ## start the wsgi (production) and development server, rebuilding the containers
	@$(COMPOSE) up --force-recreate --build -d nginx
.PHONY: run-rebuild

run: ## start the wsgi (production) and development server
	@$(COMPOSE) up --force-recreate --build -d nginx
.PHONY: run

run-with-frontend: ## Start all the containers needed (backend to frontend)
	@$(MAKE) run
	@$(COMPOSE) up --force-recreate -d frontend-dev
.PHONY: run-with-frontend

run-all-fg: ## Start backend containers and frontend in foreground
	@$(COMPOSE) up --force-recreate --build nginx frontend-dev backend-dev celery-dev
.PHONY: run-all-fg

status: ## an alias for "docker compose ps"
	@$(COMPOSE) ps
.PHONY: status

stop: ## stop the development server using Docker
	@$(COMPOSE) stop
.PHONY: stop

# -- Backend

demo: ## flush db then create a demo for load testing purpose
	@$(MAKE) resetdb
	@$(MANAGE) create_demo
.PHONY: demo

lint: \
  lint-ruff-format \
  lint-check
.PHONY: lint

## Check-only version
lint-check: \
  lint-ruff-check \
  lint-back \
  lint-mta-in \
  lint-mta-out
.PHONY: lint-check

lint-ruff-format: ## format back-end python sources with ruff
	@echo 'lint:ruff-format started…'
	@$(COMPOSE_RUN_APP_TOOLS) ruff format .
.PHONY: lint-ruff-format

lint-ruff-check: ## lint back-end python sources with ruff
	@echo 'lint:ruff-check started…'
	@$(COMPOSE_RUN_APP_TOOLS) ruff check . --fix
.PHONY: lint-ruff-check

lint-back: ## lint back-end python sources with pylint
	@echo 'lint:pylint started…'
	@$(COMPOSE_RUN_APP_TOOLS) sh -c "pylint **/*.py"
.PHONY: lint-back

lint-mta-in: ## lint mta-in python sources with pylint
	@echo 'lint:mta-in started…'
	@$(COMPOSE_RUN_MTA_IN_TESTS) ruff format .
	@$(COMPOSE_RUN_MTA_IN_TESTS) ruff check . --fix
# 	@$(COMPOSE_RUN_MTA_IN_TESTS) pylint .
.PHONY: lint-mta-in

lint-mta-out: ## lint mta-out python sources with pylint
	@echo 'lint:mta-out started…'
	@$(COMPOSE_RUN_MTA_OUT_TESTS) ruff format .
	@$(COMPOSE_RUN_MTA_OUT_TESTS) ruff check . --fix
.PHONY: lint-mta-out

test: ## run project tests
	@$(MAKE) test-back-parallel
.PHONY: test

test-back: ## run back-end tests
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	bin/pytest $${args:-${1}}
.PHONY: test-back

test-back-parallel: ## run all back-end tests in parallel
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	bin/pytest -n auto $${args:-${1}}
.PHONY: test-back-parallel

makemigrations:  ## run django makemigrations for the messages project.
	@echo "$(BOLD)Running makemigrations$(RESET)"
	@$(COMPOSE) up -d postgresql
	@$(MANAGE) makemigrations
.PHONY: makemigrations

migrate:  ## run django migrations for the messages project.
	@echo "$(BOLD)Running migrations$(RESET)"
	@$(COMPOSE) up -d postgresql
	@$(MANAGE) migrate
.PHONY: migrate

showmigrations: ## show all migrations for the messages project.
	@$(MANAGE) showmigrations
.PHONY: showmigrations

superuser: ## Create an admin superuser with password "admin"
	@echo "$(BOLD)Creating a Django superuser$(RESET)"
	@$(MANAGE) createsuperuser --email admin@example.com --password admin
.PHONY: superuser

back-i18n-compile: ## compile the gettext files
	@$(MANAGE) compilemessages --ignore="venv/**/*"
.PHONY: back-i18n-compile

back-i18n-generate: ## create the .pot files used for i18n
	@$(MANAGE) makemessages -a --keep-pot --all
.PHONY: back-i18n-generate

back-shell: ## open a shell in the backend container
	@$(COMPOSE) run --rm --build backend-dev /bin/sh
.PHONY: back-shell

back-poetry-lock: ## lock the dependencies
	@$(COMPOSE) run --rm --build backend-poetry poetry lock
.PHONY: back-poetry-lock

back-poetry-check: ## check the dependencies
	@$(COMPOSE) run --rm --build backend-poetry poetry check
.PHONY: back-poetry-check

back-poetry-outdated: ## show outdated dependencies
	@$(COMPOSE) run --rm --build backend-poetry poetry show --outdated
.PHONY: back-poetry-outdated

shell: ## connect to django shell
	@$(MANAGE) shell #_plus
.PHONY: dbshell

# -- Database

dbshell: ## connect to database shell
	docker compose exec backend-dev python manage.py dbshell
.PHONY: dbshell

resetdb: FLUSH_ARGS ?=
resetdb: ## flush database
	@echo "$(BOLD)Flush database$(RESET)"
	@$(MANAGE) flush $(FLUSH_ARGS)
.PHONY: resetdb

fullresetdb: build ## flush database, including schema
	@echo "$(BOLD)Flush database$(RESET)"
	$(MANAGE) drop_all_tables
	$(MANAGE) migrate
.PHONY: fullresetdb

env.d/development/common:
	cp -n env.d/development/common.dist env.d/development/common

env.d/development/backend:
	cp -n env.d/development/backend.dist env.d/development/backend

env.d/development/mta-in:
	cp -n env.d/development/mta-in.dist env.d/development/mta-in

env.d/development/postgresql:
	cp -n env.d/development/postgresql.dist env.d/development/postgresql

env.d/development/kc_postgresql:
	cp -n env.d/development/kc_postgresql.dist env.d/development/kc_postgresql

env.d/development/mta-out:
	cp -n env.d/development/mta-out.dist env.d/development/mta-out

# -- Internationalization

env.d/development/crowdin:
	cp -n env.d/development/crowdin.dist env.d/development/crowdin

crowdin-download: ## Download translated message from crowdin
	@$(COMPOSE_RUN_CROWDIN) download -c crowdin/config.yml
.PHONY: crowdin-download

crowdin-download-sources: ## Download sources from Crowdin
	@$(COMPOSE_RUN_CROWDIN) download sources -c crowdin/config.yml
.PHONY: crowdin-download-sources

crowdin-upload: ## Upload source translations to crowdin
	@$(COMPOSE_RUN_CROWDIN) upload sources -c crowdin/config.yml
.PHONY: crowdin-upload

i18n-compile: ## compile all translations
i18n-compile: \
	back-i18n-compile \
	frontend-i18n-compile
.PHONY: i18n-compile

i18n-generate: ## create the .pot files and extract frontend messages
i18n-generate: \
	back-i18n-generate \
	frontend-i18n-generate
.PHONY: i18n-generate

i18n-download-and-compile: ## download all translated messages and compile them to be used by all applications
i18n-download-and-compile: \
  crowdin-download \
  i18n-compile
.PHONY: i18n-download-and-compile

i18n-generate-and-upload: ## generate source translations for all applications and upload them to Crowdin
i18n-generate-and-upload: \
  i18n-generate \
  crowdin-upload
.PHONY: i18n-generate-and-upload

# -- Misc
clean: ## restore repository state as it was freshly cloned
	git clean -idx
.PHONY: clean

clean-media: ## remove all media files
	rm -rf data/media/*
.PHONY: clean-media

pyclean: ## remove all python cache files
	find . | grep -E "\(/__pycache__$|\.pyc$|\.pyo$\)" | xargs rm -rf
.PHONY: pyclean

help:
	@echo "$(BOLD)messages Makefile"
	@echo "Please use 'make $(BOLD)target$(RESET)' where $(BOLD)target$(RESET) is one of:"
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "$(GREEN)%-30s$(RESET) %s\n", $$1, $$2}'
.PHONY: help

frontend-shell: ## open a shell in the frontend container
	@$(COMPOSE) run --rm frontend-tools /bin/sh
.PHONY: frontend-shell

# Front
frontend-install: ## install the frontend locally
	@$(COMPOSE) run --rm frontend-tools npm install
.PHONY: frontend-install

frontend-install-frozen: ## install the frontend locally, following the frozen lockfile
	@$(COMPOSE) run --rm frontend-tools npm ci
.PHONY: frontend-install-frozen

frontend-build: ## build the frontend locally
	@$(COMPOSE) run --rm frontend-tools npm run build
.PHONY: frontend-build

frontend-lint: ## run the frontend linter
	@$(COMPOSE) run --rm frontend-tools npm run lint
.PHONY: frontend-lint

frontend-test: ## run the frontend tests
	@$(COMPOSE) run --rm frontend-tools npm run test
.PHONY: frontend-test

frontend-i18n-extract: ## Extract the frontend translation inside a json to be used for crowdin
	@$(COMPOSE) run --rm frontend-tools npm run i18n:extract
.PHONY: frontend-i18n-extract

frontend-i18n-generate: ## Generate the frontend json files used for crowdin
frontend-i18n-generate: \
	crowdin-download-sources \
	frontend-i18n-extract
.PHONY: frontend-i18n-generate

frontend-i18n-compile: ## Format the crowin json files used deploy to the apps
	@$(COMPOSE) run --rm frontend-tools npm run i18n:deploy
.PHONY: frontend-i18n-compile

back-api-update: ## Update the OpenAPI schema
	bin/update_openapi_schema
.PHONY: back-api-update

frontend-api-update: ## Update the frontend API client
	@$(COMPOSE) run --rm frontend-tools npm run api:update
.PHONY: frontend-api-update

api-update: ## Update the OpenAPI schema then frontend API client
api-update: \
	back-api-update \
	frontend-api-update
.PHONY: api-update

elasticsearch-index: ## Create and/or reindex elasticsearch data
	@$(MANAGE) es_create_index
	@$(MANAGE) es_reindex --all
.PHONY: elasticsearch-index