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
	@$(MAKE) install-frozen-front
.PHONY: update

# -- Docker/compose
build: ## build the project containers
	@$(COMPOSE) build
.PHONY: build

build-back-distroless: ## build the distroless production image
	@docker build --target runtime-distroless-prod -t messages-distroless -f src/backend/Dockerfile src/backend/
.PHONY: build-back-distroless

test-back-distroless: build-back-distroless ## build and smoke-test the distroless production image
	@docker run --rm messages-distroless python -c " \
		import sys, ctypes, sqlite3, ssl; \
		import magic; \
		magic.from_buffer(b'test', mime=True); \
		print(f'OK: Python {sys.version.split()[0]}, {ssl.OPENSSL_VERSION}')"
.PHONY: test-back-distroless

down: ## stop and remove containers, networks, images, and volumes
	@$(COMPOSE) down
.PHONY: down

logs: ## display all services logs (follow mode)
	@$(COMPOSE) logs -f
.PHONY: logs

start: ## start all development services
	@$(COMPOSE) up --force-recreate --build -d frontend-dev backend-dev worker-dev mta-in --wait
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

import-bucket: ## create the message imports bucket in objectstorage
	@$(COMPOSE) up -d objectstorage --wait
	@$(MANAGE_DB) create_bucket --storage message-imports --expire-days 1
.PHONY: import-bucket

shell-objectstorage: ## open a shell in the objectstorage container
	@$(COMPOSE) run --rm --build objectstorage bash
.PHONY: shell-objectstorage

# -- Linters

lint: ## run all linters
lint: \
  lint-back \
  lint-front \
  typecheck-front \
  lint-mta-in \
  lint-mta-out
.PHONY: lint

lint-check:  ## run all linters in check mode (no auto-fix)
lint-check: \
  lint-check-back \
  typecheck-front \
  lint-front
.PHONY: lint-check

lint-back: ## run back-end linters (with auto-fix)
lint-back: \
  format-back \
  check-back \
  analyze-back
.PHONY: lint-back

lint-check-back: ## run back-end linters in check mode (no auto-fix)
	@$(COMPOSE_RUN_APP_TOOLS) ruff format --check .
	@$(COMPOSE_RUN_APP_TOOLS) ruff check .
	@$(COMPOSE_RUN_APP_TOOLS) sh -c "pylint ."
.PHONY: lint-check-back

format-back: ## format back-end python sources
	@$(COMPOSE_RUN_APP_TOOLS) ruff format .
.PHONY: format-back

check-back: ## check back-end python sources
	@$(COMPOSE_RUN_APP_TOOLS) ruff check . --fix
.PHONY: check-back

analyze-back: ## analyze back-end python sources
	@$(COMPOSE_RUN_APP_TOOLS) sh -c "pylint ."
.PHONY: analyze-back

typecheck-front: ## run the frontend type checker
	@$(COMPOSE) run --rm frontend-tools npm run ts:check
.PHONY: typecheck-front

lint-front: ## run the frontend linter
	@$(COMPOSE) run --rm frontend-tools npm run lint
.PHONY: lint-front

lint-mta-in: ## lint mta-in python sources
	$(COMPOSE_RUN) --rm -e EXEC_CMD_ONLY=true mta-in-test ruff format .
	#$(COMPOSE_RUN) --rm -e EXEC_CMD_ONLY=true mta-in-test ruff check . --fix
	#$(COMPOSE_RUN) --rm -e EXEC_CMD_ONLY=true mta-in-test pylint .
.PHONY: lint-mta-in

lint-mta-out: ## lint mta-out python sources
	$(COMPOSE_RUN) --rm -e EXEC_CMD_ONLY=true mta-out-test ruff format .
.PHONY: lint-mta-out

# -- Tests

test: ## run all tests
test: \
  test-back \
  test-front \
  test-mta-in \
  test-mta-out \
  test-mpa \
  test-socks-proxy
.PHONY: test

test-back: ## run back-end tests
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	bin/pytest $${args:-${1}}
.PHONY: test-back

test-back-parallel: ## run all back-end tests in parallel
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	bin/pytest -n auto $${args:-${1}}
.PHONY: test-back-parallel

fuzz-back: ## run back-end fuzz tests
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	bin/pytest -m fuzz $${args:-${1}}
.PHONY: fuzz-back

test-front: ## run the frontend tests
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	$(COMPOSE) run --rm frontend-tools npm run test -- $${args:-${1}}
.PHONY: test-front

test-front-update: ## run the frontend tests and update snapshots
	$(COMPOSE) run --rm frontend-tools npm run test -- --update
.PHONY: test-front-update

test-front-amd64: ## run the frontend tests in amd64
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	$(COMPOSE) run --rm frontend-tools-amd64 npm run test -- $${args:-${1}}
.PHONY: test-front-amd64

test-mta-in: ## run the mta-in tests
	@$(COMPOSE) run --build --rm mta-in-test
.PHONY: test-mta-in

test-mta-out: ## run the mta-out tests
	@$(COMPOSE) run --build --rm mta-out-test
.PHONY: test-mta-out

test-mpa: ## run the mpa tests
	@$(COMPOSE) run --build --rm mpa-test
.PHONY: test-mpa

test-socks-proxy: ## run the socks-proxy tests
	@$(COMPOSE) run --build --rm socks-proxy-test
.PHONY: test-socks-proxy

# -- E2E Tests

test-e2e: ## Setup, run and teardown e2e tests in headless mode
	@$(MAKE) start-e2e
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	$(MAKE) test-e2e-bare args="$${args:-${1}}" || echo "$(BOLD)Tests failed$(RESET)"
	@$(MAKE) stop-e2e
.PHONY: test-e2e

test-e2e-ui: ## Setup, run and teardown e2e tests in UI mode
	@$(MAKE) start-e2e
	@$(MAKE) test-e2e-ui-bare
	@$(MAKE) stop-e2e
.PHONY: test-e2e-ui

test-e2e-dev: ## Setup, run and teardown e2e tests in UI mode with dev frontend
	@$(MAKE) start-e2e
	@$(MAKE) test-e2e-dev-bare
	@$(MAKE) stop-e2e
.PHONY: test-e2e-dev

test-e2e-ci: ## Setup and run e2e tests in CI mode
	@$(MAKE) start-e2e
	@$(MAKE) test-e2e-bare args="$(args)"
.PHONY: test-e2e-ci

build-e2e: ## Build the e2e services
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	$(COMPOSE_E2E) build --no-cache $${args:-${1}}
.PHONY: build-e2e

log-e2e: ## alias for logs-e2e
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	$(MAKE) logs-e2e -- $${args:-${1}}
.PHONY: log-e2e

logs-e2e: ## Show logs from e2e services
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	$(COMPOSE_E2E) --profile dev logs $${args:-${1}}
.PHONY: logs-e2e

test-e2e-bare: ## Run e2e tests in headless mode
	@echo "$(BLUE)\n\n| 🎭 Running E2E tests... \n$(RESET)"
	$(COMPOSE_E2E) run --rm --service-ports runner npm run test -- $(args)
	@echo "$(GREEN)> 🎭 E2E tests completed!$(RESET)\n"
.PHONY: test-e2e-bare

test-e2e-ui-bare: ## Run e2e tests in UI mode
	@echo "$(BLUE)\n\n| 🎭 Running E2E tests in UI mode... \n$(RESET)"
	# Note: || true allows graceful exit when user closes the UI
	@$(COMPOSE_E2E) run --rm --service-ports runner npm run test:ui || true
	@echo "$(GREEN)> 🎭 You killed the UI!$(RESET)\n"
.PHONY: test-e2e-ui-bare

test-e2e-dev-bare: ## Run e2e tests in UI mode with dev frontend
	@echo "$(BLUE)\n\n| 🎭 Running E2E tests in dev mode... \n$(RESET)"
	# Note: || true allows graceful exit when user closes the UI
	E2E_PROFILE=dev $(COMPOSE_E2E) --profile dev run --rm --service-ports runner npm run test:ui || true
	@echo "$(GREEN)> 🎭 You killed the UI!$(RESET)\n"
.PHONY: test-e2e-dev-bare

down-e2e: stop-e2e ## alias for stop-e2e
.PHONY: down-e2e

demo-e2e: ## Populate the e2e database with demo data
	@echo "$(BLUE)\n\n| 📝 Bootstrapping E2E demo data... \n$(RESET)"
	@$(COMPOSE_E2E) run --rm backend python manage.py e2e_demo
.PHONY: demo-e2e

start-e2e: ## Start e2e services (migrate, seed, etc.)
	@echo "$(BLUE)\n\n| 🔧 Setting up E2E services... \n$(RESET)"
	@$(COMPOSE_E2E) run --rm backend python manage.py create_bucket --storage message-imports --expire-days 1
	@$(COMPOSE_E2E) run --rm backend python manage.py migrate --noinput
	@$(COMPOSE_E2E) run --rm backend python manage.py search_index_create || true
	@$(MAKE) demo-e2e
.PHONY: start-e2e

stop-e2e: ## Stop and remove e2e services
	@echo "$(BLUE)\n\n| 🧹 Cleaning up E2E services... \n$(RESET)"
	@$(COMPOSE_E2E) --profile dev down -v
.PHONY: stop-e2e

# -- Backend


migrations:  ## run django makemigrations for the messages project.
	@echo "$(BOLD)Running makemigrations$(RESET)"
	@$(MANAGE_DB) makemigrations
.PHONY: migrations


migrations-check:  ## check that all model changes have corresponding migrations.
	@echo "$(BOLD)Checking migrations$(RESET)"
	@$(COMPOSE_RUN_APP_TOOLS) python manage.py makemigrations --check --dry-run
.PHONY: migrations-check

migrate:  ## run django migrations for the messages project.
	@echo "$(BOLD)Running migrations$(RESET)"
	@$(MANAGE_DB) migrate
.PHONY: migrate

showmigrations: ## show all migrations for the messages project.
	@$(MANAGE_DB) showmigrations
.PHONY: showmigrations

superuser: ## Create an admin superuser with password "admin" and promote user1 as superuser
	@echo "$(BOLD)Creating a Django superuser$(RESET)"
	@$(MANAGE_DB) createsuperuser --email admin@admin.local --password admin
	@$(MANAGE_DB) createsuperuser --email user1@example.local --password user1
.PHONY: superuser

shell-back: ## open a shell in the backend container
	@$(COMPOSE) run --rm --build backend-dev /bin/bash
.PHONY: shell-back

shell-back-no-deps: ## open a shell in the backend container without dependencies
	@$(COMPOSE) run --rm --no-deps --build backend-dev /bin/bash
.PHONY: shell-back-no-deps

exec-back: ## open a shell in the running backend-dev container
	@$(COMPOSE) exec backend-dev /bin/bash
.PHONY: exec-back

deps-lock-back: ## lock the dependencies
	@$(COMPOSE) run --rm --build backend-uv uv lock
	@$(MAKE) deps-audit
.PHONY: deps-lock-back

deps-update-indirect-back: ## update indirect dependencies
	rm -f src/backend/uv.lock
	@$(MAKE) deps-lock-back
.PHONY: deps-update-indirect-back

deps-outdated-back: ## show outdated dependencies
	@$(COMPOSE) run --rm --build backend-uv uv tree --outdated
.PHONY: deps-outdated-back

deps-tree-back: ## show dependencies as a tree
	@$(COMPOSE) run --rm --build backend-uv uv tree
.PHONY: deps-tree-back

deps-audit-back: ## audit back-end dependencies for vulnerabilities
	@$(COMPOSE) run --rm --no-deps -e HOME=/tmp --build backend-dev pip-audit
.PHONY: deps-audit-back

deps-audit: deps-audit-back ## alias for deps-audit-back
.PHONY: deps-audit

collectstatic: ## collect static files
	@$(MANAGE_DB) collectstatic --noinput
.PHONY: collectstatic

shell-back-django: ## connect to django shell
	@$(MANAGE) shell #_plus
.PHONY: shell-back-django

export-identity: ## export all identity provider data to a JSON file
	@$(COMPOSE) run -v `pwd`/src/keycloak:/tmp/keycloak-export --rm keycloak export --realm messages --file /tmp/keycloak-export/realm.json
.PHONY: export-identity

# -- Database

shell-db: ## connect to database shell
	$(COMPOSE) exec backend-dev python manage.py dbshell
.PHONY: shell-db

reset-db: FLUSH_ARGS ?=
reset-db: ## flush database
	@echo "$(BOLD)Flush database$(RESET)"
	@$(MANAGE_DB) flush $(FLUSH_ARGS)
.PHONY: reset-db

reset-db-full: build ## flush database, including schema
	@echo "$(BOLD)Flush database$(RESET)"
	$(MANAGE_DB) drop_all_tables
	$(MANAGE_DB) migrate
.PHONY: reset-db-full

env.d/development/%.local:
	@echo "# Local development overrides for $(notdir $*)" > $@
	@echo "# Add your local-specific environment variables below:" >> $@
	@echo "# Example: DJANGO_DEBUG=True" >> $@
	@echo "" >> $@


# -- Internationalization

i18n-download: ## Download translated messages
	@$(COMPOSE_RUN_CROWDIN) download -c crowdin/config.yml
.PHONY: i18n-download

i18n-download-sources: ## Download translation sources
	@$(COMPOSE_RUN_CROWDIN) download sources -c crowdin/config.yml
.PHONY: i18n-download-sources

i18n-upload: ## Upload source translations
	@$(COMPOSE_RUN_CROWDIN) upload sources -c crowdin/config.yml
.PHONY: i18n-upload

i18n-generate: ## extract frontend messages for translation
i18n-generate: \
	i18n-generate-front
.PHONY: i18n-generate

i18n-download-and-compile: ## download all translated messages to be used by all applications
i18n-download-and-compile: \
  i18n-download
.PHONY: i18n-download-and-compile

i18n-generate-and-upload: ## generate source translations for all applications and upload them to Crowdin
i18n-generate-and-upload: \
  i18n-generate \
  i18n-upload
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

clean-cache: ## remove all python cache files
	find . | grep -E "\(/__pycache__$|\.pyc$|\.pyo$\)" | xargs rm -rf
.PHONY: clean-cache

help:
	@echo "$(BOLD)messages Makefile"
	@echo "Please use 'make $(BOLD)target$(RESET)' where $(BOLD)target$(RESET) is one of:"
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "$(GREEN)%-30s$(RESET) %s\n", $$1, $$2}'
.PHONY: help

shell-front: ## open a shell in the frontend container
	@$(COMPOSE) run --rm --build frontend-tools /bin/sh
.PHONY: shell-front

# Front
install-front: ## install the frontend locally
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	$(COMPOSE) run --rm --build frontend-tools npm install $${args:-${1}}
.PHONY: install-front

install-frozen-front: ## install the frontend locally, following the frozen lockfile
	@echo "Installing frontend dependencies, this might take a few minutes..."
	@$(COMPOSE) run --rm --build frontend-tools npm ci
.PHONY: install-frozen-front

install-frozen-front-amd64: ## install the frontend locally, following the frozen lockfile
	@$(COMPOSE) run --rm --build frontend-tools-amd64 npm ci
.PHONY: install-frozen-front-amd64

build-front: ## build the frontend locally
	@$(COMPOSE) run --rm --build frontend-tools npm run build
.PHONY: build-front

i18n-generate-front: ## Extract the frontend translation inside a json to be used for crowdin
	@$(COMPOSE) run --rm --build frontend-tools npm run i18n:extract
.PHONY: i18n-generate-front

api-update-back: ## Update the OpenAPI schema
	bin/update_openapi_schema
.PHONY: api-update-back

api-update-front: ## Update the frontend API client
	@$(COMPOSE) run --rm --build frontend-tools npm run api:update
.PHONY: api-update-front

api-update: ## Update the OpenAPI schema then frontend API client
api-update: \
	api-update-back \
	api-update-front
.PHONY: api-update

search-index: ## Create and/or reindex opensearch data
	@$(MANAGE) search_reindex --all --recreate-index
.PHONY: search-index

deps-lock-mta-in: ## lock the dependencies
	@$(COMPOSE) run --rm --build mta-in-uv uv lock
.PHONY: deps-lock-mta-in

deps-lock-mta-out: ## lock the dependencies
	@$(COMPOSE) run --rm --build mta-out-uv uv lock
.PHONY: deps-lock-mta-out
