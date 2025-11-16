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
BLUE := \033[1;34m

# -- Docker
# Get the current user ID to use for docker run and docker exec commands
DOCKER_UID          = $(shell id -u)
DOCKER_GID          = $(shell id -g)
DOCKER_USER         = $(DOCKER_UID):$(DOCKER_GID)
COMPOSE             = DOCKER_USER=$(DOCKER_USER) docker compose
COMPOSE_E2E         = DOCKER_USER=$(DOCKER_USER) docker compose -f src/e2e/compose.yaml
COMPOSE_EXEC        = $(COMPOSE) exec
COMPOSE_EXEC_APP    = $(COMPOSE_EXEC) backend-dev
COMPOSE_RUN         = $(COMPOSE) run --rm --build
COMPOSE_RUN_APP     = $(COMPOSE_RUN) backend-dev
COMPOSE_RUN_APP_DB  = $(COMPOSE_RUN) backend-db
COMPOSE_RUN_APP_TOOLS = $(COMPOSE_RUN) --no-deps backend-dev
COMPOSE_RUN_CROWDIN = $(COMPOSE_RUN) crowdin crowdin

# -- Backend
MANAGE              = $(COMPOSE_RUN_APP) python manage.py
MANAGE_DB           = $(COMPOSE_RUN_APP_DB) python manage.py


# ==============================================================================
# RULES

default: help

data/media:
	@mkdir -p data/media

data/static:
	@mkdir -p data/static

# -- Project

create-env-files: ## Create empty .local env files for local development
create-env-files: \
	env.d/development/crowdin.local \
	env.d/development/postgresql.local \
	env.d/development/keycloak.local \
	env.d/development/backend.local \
	env.d/development/frontend.local \
	env.d/development/mta-in.local \
	env.d/development/mta-out.local \
	env.d/development/socks-proxy.local
.PHONY: create-env-files

bootstrap: ## Prepare the project for local development
	@echo "$(BOLD)"
	@echo "╔══════════════════════════════════════════════════════════════════════════════╗"
	@echo "║                                                                              ║"
	@echo "║  🚀 Welcome to Messages - Collaborative Inbox from La Suite! 🚀              ║"
	@echo "║                                                                              ║"
	@echo "║  This will set up your development environment with :                        ║"
	@echo "║  • Docker containers for all services                                        ║"
	@echo "║  • Database migrations and static files                                      ║"
	@echo "║  • Frontend dependencies and build                                           ║"
	@echo "║  • Environment configuration files                                           ║"
	@echo "║                                                                              ║"
	@echo "║  Services will be available at:                                              ║"
	@echo "║  • Frontend: http://localhost:8900                                           ║"
	@echo "║  • API:      http://localhost:8901                                           ║"
	@echo "║  • Admin:    http://localhost:8901/admin                                     ║"
	@echo "║                                                                              ║"
	@echo "╚══════════════════════════════════════════════════════════════════════════════╝"
	@echo "$(RESET)"
	@echo "$(GREEN)Starting bootstrap process...$(RESET)"
	@echo ""
	@$(MAKE) update
	@$(MAKE) superuser
	@$(MAKE) start
	@echo ""
	@echo "$(GREEN)🎉 Bootstrap completed successfully!$(RESET)"
	@echo ""
	@echo "$(BOLD)Next steps:$(RESET)"
	@echo "  • Visit http://localhost:8900 to access the application"
	@echo "  • Run 'make help' to see all available commands"
	@echo ""
.PHONY: bootstrap

update:  ## Update the project with latest changes
	@$(MAKE) data/media
	@$(MAKE) data/static
	@$(MAKE) import-bucket
	@$(MAKE) create-env-files
	@$(MAKE) build
	@$(MAKE) collectstatic
	@$(MAKE) migrate
	@$(MAKE) front-install-frozen
	@$(MAKE) i18n-compile
.PHONY: update

# -- Docker/compose
build: ## build the project containers
	@$(COMPOSE) build
.PHONY: build

down: ## stop and remove containers, networks, images, and volumes
	@$(COMPOSE) down
.PHONY: down

logs: ## display all services logs (follow mode)
	@$(COMPOSE) logs -f
.PHONY: logs

start: ## start all development services
	@$(COMPOSE) up --force-recreate --build -d frontend-dev backend-dev celery-dev mta-in --wait
.PHONY: start

start-minimal: ## start minimal services (backend, frontend, keycloak and DB)
	@$(COMPOSE) up --force-recreate --build -d backend-db frontend-dev keycloak --wait
.PHONY: start-minimal

status: ## an alias for "docker compose ps"
	@$(COMPOSE) ps
.PHONY: status

stop: ## stop all development services
	@$(COMPOSE) --profile "*" stop
.PHONY: stop

restart: ## restart all development services
restart: \
	stop \
	start
.PHONY: restart

restart-minimal: ## restart minimal services
restart-minimal: \
	stop \
	start-minimal
.PHONY: restart-minimal

import-bucket: ## create the message imports bucket to objectstorage
	@$(COMPOSE_RUN) objectstorage-createbucket
.PHONY: import-bucket

objectstorage-shell: ## open a shell in the objectstorage container
	@$(COMPOSE) run --rm --build objectstorage bash
.PHONY: objectstorage-shell

# -- Linters

lint: ## run all linters
lint: \
  back-lint \
  front-lint \
  front-ts-check \
  mta-in-lint \
  mta-out-lint
.PHONY: lint

lint-check:  ## run all linters in check mode
lint-check: \
  back-ruff-check \
  back-pylint \
  front-ts-check \
  front-lint
.PHONY: lint-check

back-lint: ## run back-end linters
back-lint: \
  back-ruff-format \
  back-ruff-check \
  back-pylint
.PHONY: back-lint

back-ruff-format: ## format back-end python sources with ruff
	@$(COMPOSE_RUN_APP_TOOLS) ruff format .
.PHONY: back-ruff-format

back-ruff-check: ## lint back-end python sources with ruff
	@$(COMPOSE_RUN_APP_TOOLS) ruff check . --fix
.PHONY: back-ruff-check

back-pylint: ## lint back-end python sources with pylint
	@$(COMPOSE_RUN_APP_TOOLS) sh -c "pylint ."
.PHONY: back-pylint

front-ts-check: ## run the frontend type checker
	@$(COMPOSE) run --rm frontend-tools npm run ts:check
.PHONY: front-ts-check

front-lint: ## run the frontend linter
	@$(COMPOSE) run --rm frontend-tools npm run lint
.PHONY: front-lint

mta-in-lint: ## lint mta-in python sources with pylint
	$(COMPOSE_RUN) --rm -e EXEC_CMD_ONLY=true mta-in-test ruff format .
	#$(COMPOSE_RUN) --rm -e EXEC_CMD_ONLY=true mta-in-test ruff check . --fix
	#$(COMPOSE_RUN) --rm -e EXEC_CMD_ONLY=true mta-in-test pylint .
.PHONY: mta-in-lint

mta-out-lint: ## lint mta-out python sources with pylint
	$(COMPOSE_RUN) --rm -e EXEC_CMD_ONLY=true mta-out-test ruff format .
.PHONY: mta-out-lint

# -- Tests

test: ## run all tests
test: \
  back-test \
  front-test \
  mta-in-test \
  mta-out-test \
  mpa-test \
  socks-proxy-test
.PHONY: test

back-test: ## run back-end tests
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	bin/pytest $${args:-${1}}
.PHONY: back-test

back-test-parallel: ## run all back-end tests in parallel
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	bin/pytest -n auto $${args:-${1}}
.PHONY: back-test-parallel

front-test: ## run the frontend tests
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	$(COMPOSE) run --rm frontend-tools npm run test -- $${args:-${1}}
.PHONY: front-test

front-test-amd64: ## run the frontend tests in amd64
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	$(COMPOSE) run --rm frontend-tools-amd64 npm run test -- $${args:-${1}}
.PHONY: front-test-amd64

mta-in-test: ## run the mta-in tests
	@$(COMPOSE) run --build --rm mta-in-test
.PHONY: mta-in-test

mta-out-test: ## run the mta-out tests
	@$(COMPOSE) run --build --rm mta-out-test
.PHONY: mta-out-test

mpa-test: ## run the mpa tests
	@$(COMPOSE) run --build --rm mpa-test
.PHONY: mpa-test

socks-proxy-test: ## run the socks-proxy tests
	@$(COMPOSE) run --build --rm socks-proxy-test
.PHONY: socks-proxy-test

# -- E2E Tests

e2e-test: ## Setup, run and teardown e2e tests in headless mode
	@$(MAKE) e2e-setup
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	$(MAKE) e2e-run-test args="$${args:-${1}}" || echo "$(BOLD)Tests failed$(RESET)"
	@$(MAKE) e2e-teardown
.PHONY: e2e-test

e2e-test-ui: ## Setup, run and teardown e2e tests in UI mode
	@$(MAKE) e2e-setup
	@$(MAKE) e2e-run-test-ui
	@$(MAKE) e2e-teardown
.PHONY: e2e-test-ui

e2e-test-dev: ## Setup, run and teardown e2e tests in UI mode with dev frontend
	@$(MAKE) e2e-setup
	@$(MAKE) e2e-run-test-dev
	@$(MAKE) e2e-teardown
.PHONY: e2e-test-dev

e2e-test-ci: ## Setup and run e2e tests in CI mode
	@$(MAKE) e2e-setup
	@$(MAKE) e2e-run-test args="$(args)"
.PHONY: e2e-test-ci

e2e-build: ## Build the e2e services
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	$(COMPOSE_E2E) build --no-cache $${args:-${1}}
.PHONY: e2e-build

e2e-log:
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	$(MAKE) e2e-logs -- $${args:-${1}}
.PHONY: e2e-log

e2e-logs: ## Show logs from e2e services
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	$(COMPOSE_E2E) --profile dev logs $${args:-${1}}
.PHONY: e2e-logs

e2e-run-test: ## Run e2e tests in headless mode
	@echo "$(BLUE)\n\n| 🎭 Running E2E tests... \n$(RESET)"
	$(COMPOSE_E2E) run --rm --service-ports runner npm run test -- $(args)
	@echo "$(GREEN)> 🎭 E2E tests completed!$(RESET)\n"
.PHONY: e2e-run-test

e2e-run-test-ui: ## Run e2e tests in UI mode
	@echo "$(BLUE)\n\n| 🎭 Running E2E tests in UI mode... \n$(RESET)"
	# Note: || true allows graceful exit when user closes the UI
	@$(COMPOSE_E2E) run --rm --service-ports runner npm run test:ui || true
	@echo "$(GREEN)> 🎭 You killed the UI!$(RESET)\n"
.PHONY: e2e-run-test-ui

e2e-run-test-dev: ## Run e2e tests in UI mode with dev frontend
	@echo "$(BLUE)\n\n| 🎭 Running E2E tests in dev mode... \n$(RESET)"
	# Note: || true allows graceful exit when user closes the UI
	E2E_PROFILE=dev $(COMPOSE_E2E) --profile dev run --rm --service-ports runner npm run test:ui || true
	@echo "$(GREEN)> 🎭 You killed the UI!$(RESET)\n"
.PHONY: e2e-run-test-dev

e2e-down: ## Stop and remove all e2e services
	@echo "$(BOLD)Stopping E2E services...$(RESET)"
	@$(COMPOSE_E2E) --profile dev down -v
	@echo "$(GREEN)✓ E2E services stopped$(RESET)"
.PHONY: e2e-down

e2e-demo: ## Populate the e2e database with demo data
	@echo "$(BLUE)\n\n| 📝 Bootstrapping E2E demo data... \n$(RESET)"
	@$(COMPOSE_E2E) run --rm backend python manage.py e2e_demo
.PHONY: e2e-demo

e2e-setup: ## Setup e2e services
	@echo "$(BLUE)\n\n| 🔧 Setting up E2E services... \n$(RESET)"
	@$(COMPOSE_E2E) run --rm objectstorage-createbucket
	@$(COMPOSE_E2E) run --rm backend python manage.py migrate --noinput
	@$(COMPOSE_E2E) run --rm backend python manage.py search_index_create || true
	@$(MAKE) e2e-demo
.PHONY: e2e-setup

e2e-teardown: ## Teardown e2e services
	@echo "$(BLUE)\n\n| 🧹 Cleaning up E2E services... \n$(RESET)"
	@$(COMPOSE_E2E) --profile dev down -v
.PHONY: e2e-teardown

# -- Backend

migrations:  ## run django makemigrations for the messages project.
	@echo "$(BOLD)Running makemigrations$(RESET)"
	@$(MANAGE_DB) makemigrations
.PHONY: migrations

migrate:  ## run django migrations for the messages project.
	@echo "$(BOLD)Running migrations$(RESET)"
	@$(MANAGE_DB) migrate
.PHONY: migrate

showmigrations: ## show all migrations for the messages project.
	@$(MANAGE_DB) showmigrations
.PHONY: showmigrations

superuser: ## Create an admin superuser with password "admin"
	@echo "$(BOLD)Creating a Django superuser$(RESET)"
	@$(MANAGE_DB) createsuperuser --email admin@admin.local --password admin
.PHONY: superuser

back-i18n-compile: ## compile the gettext files
	@$(MANAGE) compilemessages --ignore="venv/**/*"
.PHONY: back-i18n-compile

back-i18n-generate: ## create the .pot files used for i18n
	@$(MANAGE) makemessages -a --keep-pot --all --no-location
.PHONY: back-i18n-generate

back-shell: ## open a shell in the backend container
	@$(COMPOSE) run --rm --build backend-dev /bin/bash
.PHONY: back-shell

back-shell-no-deps: ## open a shell in the backend container without dependencies
	@$(COMPOSE) run --rm --no-deps --build backend-dev /bin/bash
.PHONY: back-shell-no-deps

back-exec: ## open a shell in the running backend-dev container
	@$(COMPOSE) exec backend-dev /bin/bash
.PHONY: back-exec

back-poetry-lock: ## lock the dependencies
	@$(COMPOSE) run --rm --build backend-poetry poetry lock
	make pip-audit
.PHONY: back-poetry-lock

back-poetry-update-indirect: ## update indirect dependencies
	rm src/backend/poetry.lock
	make back-poetry-lock
.PHONY: back-poetry-update-indirect
back-poetry-check: ## check the dependencies
	@$(COMPOSE) run --rm --build backend-poetry poetry check
.PHONY: back-poetry-check

back-poetry-outdated: ## show outdated dependencies
	@$(COMPOSE) run --rm --build backend-poetry poetry show --outdated
.PHONY: back-poetry-outdated

back-poetry-tree: ## show dependencies as a tree
	@$(COMPOSE) run --rm --build backend-dev pipdeptree
.PHONY: back-poetry-tree

pip-audit: ## check the dependencies
	@$(COMPOSE) run --rm --no-deps -e HOME=/tmp --build backend-dev pip-audit
.PHONY: pip-audit

collectstatic: ## collect static files
	@$(MANAGE_DB) collectstatic --noinput
.PHONY: collectstatic

shell: ## connect to django shell
	@$(MANAGE) shell #_plus
.PHONY: shell

keycloak-export: ## export all keycloak data to a JSON file
	@$(COMPOSE) run -v `pwd`/src/keycloak:/tmp/keycloak-export --rm keycloak export --realm messages --file /tmp/keycloak-export/realm.json
.PHONY: keycloak-export

# -- Database

db-shell: ## connect to database shell
	$(COMPOSE) exec backend-dev python manage.py dbshell
.PHONY: db-shell

db-reset: FLUSH_ARGS ?=
db-reset: ## flush database
	@echo "$(BOLD)Flush database$(RESET)"
	@$(MANAGE_DB) flush $(FLUSH_ARGS)
.PHONY: db-reset

db-reset-full: build ## flush database, including schema
	@echo "$(BOLD)Flush database$(RESET)"
	$(MANAGE_DB) drop_all_tables
	$(MANAGE_DB) migrate
.PHONY: db-reset-full

env.d/development/%.local:
	@echo "# Local development overrides for $(notdir $*)" > $@
	@echo "# Add your local-specific environment variables below:" >> $@
	@echo "# Example: DJANGO_DEBUG=True" >> $@
	@echo "" >> $@


# -- Internationalization

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
	back-i18n-compile
.PHONY: i18n-compile

i18n-generate: ## create the .pot files and extract frontend messages
i18n-generate: \
	back-i18n-generate \
	front-i18n-generate
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

# -- Release
release: ## Create a new release (interactive: asks for version and kind)
	bin/release.py
.PHONY: release

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

front-shell: ## open a shell in the frontend container
	@$(COMPOSE) run --rm --build frontend-tools bash
.PHONY: front-shell

# Front
front-install: ## install the frontend locally
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	$(COMPOSE) run --rm --build frontend-tools npm install $${args:-${1}}
.PHONY: front-install

front-install-frozen: ## install the frontend locally, following the frozen lockfile
	@echo "Installing frontend dependencies, this might take a few minutes..."
	@$(COMPOSE) run --rm --build frontend-tools npm ci
.PHONY: front-install-frozen

front-install-frozen-amd64: ## install the frontend locally, following the frozen lockfile
	@$(COMPOSE) run --rm --build frontend-tools-amd64 npm ci
.PHONY: front-install-frozen-amd64

front-build: ## build the frontend locally
	@$(COMPOSE) run --rm --build frontend-tools npm run build
.PHONY: front-build

front-i18n-generate: ## Extract the frontend translation inside a json to be used for crowdin
	@$(COMPOSE) run --rm --build frontend-tools npm run i18n:extract
.PHONY: front-i18n-extract

back-api-update: ## Update the OpenAPI schema
	bin/update_openapi_schema
.PHONY: back-api-update

front-api-update: ## Update the frontend API client
	@$(COMPOSE) run --rm --build frontend-tools npm run api:update
.PHONY: front-api-update

api-update: ## Update the OpenAPI schema then frontend API client
api-update: \
	back-api-update \
	front-api-update
.PHONY: api-update

search-index: ## Create and/or reindex opensearch data
	@$(MANAGE) search_index_create
	@$(MANAGE) search_reindex --all
.PHONY: search-index

mta-in-poetry-lock: ## lock the dependencies
	@$(COMPOSE) run --rm --build mta-in-poetry poetry lock
.PHONY: mta-in-poetry-lock

mta-out-poetry-lock: ## lock the dependencies
	@$(COMPOSE) run --rm --build mta-out-poetry poetry lock
.PHONY: mta-out-poetry-lock

