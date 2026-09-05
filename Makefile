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
COMPOSE             = DOCKER_USER=$(DOCKER_USER) DOCKER_UID=$(DOCKER_UID) docker compose
# Local tag for the shared base image (deploy/python-uv). `build-python-base`
# builds it, and the Dockerfiles default `FROM ${PYTHON_UV_IMAGE}` to this same
# tag, so compose builds resolve it. (CI publishes/overrides the base via the
# docker-publish workflow build-args, not this variable — overriding it here
# would only retag the local build, leaving the compose `FROM` unchanged.)
PYTHON_UV_IMAGE     ?= messages-python-uv:local
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
	deploy/env/crowdin.local \
	deploy/env/postgresql.local \
	deploy/env/keycloak.local \
	deploy/env/backend.local \
	deploy/env/frontend.local \
	deploy/env/mta-in.local \
	deploy/env/mta-in-py.local \
	deploy/env/socks-proxy.local
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
	@$(MAKE) create-env-files
	@$(MAKE) start-deps
	@$(MAKE) update
	@$(MAKE) superuser
	@$(MAKE) start
	@echo ""
	@echo "$(GREEN)🎉 Bootstrap completed successfully!$(RESET)"
	@echo ""
	@echo "$(BOLD)Next steps:$(RESET)"
	@echo "  • Visit http://localhost:8900 to access the application"
	@echo "  • Run 'make help' to see all available commands"
	@echo "  • Need search, object storage or the MTAs? Run 'make bootstrap-full'"
	@echo ""
.PHONY: bootstrap

bootstrap-full: ## Prepare the project for local development with the full stack
	@echo "$(GREEN)Starting full bootstrap process...$(RESET)"
	@echo ""
	@$(MAKE) create-env-files
	@$(MAKE) start-deps
	@$(MAKE) update-full
	@$(MAKE) superuser
	@$(MAKE) start-full
	@echo ""
	@echo "$(GREEN)🎉 Full bootstrap completed successfully!$(RESET)"
	@echo ""
.PHONY: bootstrap-full

update:  ## Update the project with latest changes (light stack; run this when pulling code)
	@$(MAKE) data/media
	@$(MAKE) data/static
	@$(MAKE) create-env-files
	@$(MAKE) collectstatic
	@$(MAKE) migrate
	@$(MAKE) install-frozen-front
.PHONY: update

update-full:  ## Update the project with latest changes incl. object-storage buckets (full stack)
update-full: \
	update \
	create-buckets
.PHONY: update-full

# -- Docker/compose
build: build-python-base ## build the project containers
	@$(COMPOSE) build
.PHONY: build

build-back-distroless: build-python-base ## build the distroless production image
	@docker buildx build --load --target runtime-distroless-prod -t messages-distroless \
		-f src/backend/Dockerfile \
		src/backend/
.PHONY: build-back-distroless

test-back-distroless: build-back-distroless ## build and smoke-test the distroless production image
	@docker run --rm messages-distroless python -c " \
		import sys, ctypes, sqlite3, ssl; \
		import magic; \
		magic.from_buffer(b'test', mime=True); \
		print(f'OK: Python {sys.version.split()[0]}, {ssl.OPENSSL_VERSION}')"
.PHONY: test-back-distroless

build-front-distroless: ## build the frontend distroless production image (Caddy + static bundle)
	@docker build --target runtime-prod -t messages-frontend-distroless src/frontend/
.PHONY: build-front-distroless

test-front-distroless: build-front-distroless ## build and smoke-test the frontend distroless production image
	@bin/smoke-test-front messages-frontend-distroless
.PHONY: test-front-distroless

build-pymta-distroless: build-python-base ## build the pymta distroless production image
	@docker build --target runtime-distroless-prod -t messages-pymta-distroless -f src/mta-in/Dockerfile.pymta src/mta-in/
.PHONY: build-pymta-distroless

test-pymta-distroless: build-pymta-distroless ## build and smoke-test the pymta distroless production image
	@docker run --rm messages-pymta-distroless python -c " \
		import sys, ssl; \
		import pymta.settings; \
		print(f'OK: Python {sys.version.split()[0]}, {ssl.OPENSSL_VERSION}, pymta.settings loaded')"
.PHONY: test-pymta-distroless

down: ## stop and remove containers, networks, images, and volumes
	@$(COMPOSE) down
.PHONY: down

logs: ## display all services logs (follow mode)
	@$(COMPOSE) logs -f
.PHONY: logs

build-python-base: ## build the shared python+uv base image (deploy/python-uv) that the backend and MTA images inherit from
	@docker build -t $(PYTHON_UV_IMAGE) deploy/python-uv
.PHONY: build-python-base

start-deps: ## start the slow infra deps (postgres, redis, keycloak) in the background so they warm up while the rest of bootstrap runs
	@$(COMPOSE) up -d --no-recreate postgresql redis keycloak
.PHONY: start-deps

# Fail fast (before booting a broken stack) when the project has not been
# bootstrapped: `make bootstrap` creates the gitignored env files and the
# frontend node_modules volume. start/start-full depend on this.
check-bootstrapped:
	@test -f deploy/env/backend.local || { \
		printf "\n$(BOLD)✗ Not bootstrapped$(RESET): env files are missing.\n  Run $(BOLD)make bootstrap$(RESET) first.\n\n" >&2; exit 1; }
	@docker volume inspect st-messages_frontend-node-modules >/dev/null 2>&1 || { \
		printf "\n$(BOLD)✗ Not bootstrapped$(RESET): frontend dependencies are not installed.\n  Run $(BOLD)make bootstrap$(RESET) first.\n\n" >&2; exit 1; }
.PHONY: check-bootstrapped

start: check-bootstrapped build-python-base ## start the light dev stack (backend, worker, frontend, keycloak, postgresql, redis)
	@$(COMPOSE) stop backend-dev worker-dev worker-ui opensearch objectstorage mailcatcher mta-in-py mpa >/dev/null 2>&1 || true
	@$(COMPOSE) up --build -d --wait \
		postgresql \
		redis \
		keycloak \
		frontend-dev \
		backend-dev-light \
		worker-dev-light
.PHONY: start

start-full: check-bootstrapped build-python-base ## start the full dev stack (adds OpenSearch, object storage, mailcatcher and the MTAs)
	@$(COMPOSE) stop backend-dev-light worker-dev-light >/dev/null 2>&1 || true
	@$(COMPOSE) up --build -d --wait \
		postgresql \
		redis \
		opensearch \
		objectstorage \
		mailcatcher \
		keycloak \
		frontend-dev \
		backend-dev \
		worker-dev \
		worker-ui \
		mta-in-py \
		mpa
.PHONY: start-full

status: ## an alias for "docker compose ps"
	@$(COMPOSE) ps
.PHONY: status

stop: ## stop all development services
	@$(COMPOSE) --profile "*" stop
.PHONY: stop

restart: ## restart the light dev stack
restart: \
	stop \
	start
.PHONY: restart

restart-full: ## restart the full dev stack
restart-full: \
	stop \
	start-full
.PHONY: restart-full

create-buckets: ## create the message imports & blobs buckets in objectstorage
	@$(COMPOSE) up -d objectstorage --wait
	@$(MANAGE_DB) create_bucket --storage message-imports --expire-days 7
	@$(MANAGE_DB) create_bucket --storage message-blobs
.PHONY: create-buckets

shell-objectstorage: ## open a shell in the objectstorage container
	@$(COMPOSE) run --rm --build objectstorage bash
.PHONY: shell-objectstorage

# Generate a per-instance OTA signing key pair. Prints the base64 PEMs to stdout:
# MOBILE_OTA_SIGNING_PUBLIC_KEY_B64 (baked into the app) + MOBILE_OTA_SIGNING_PRIVATE_KEY_B64
# (publish secret). Each deployment runs it once; the private half is a CI secret,
# never committed. No object storage needed — pure key generation.
mobile-ota-keygen: ## generate a per-instance OTA signing key pair (base64 PEMs)
	@$(COMPOSE_RUN) --no-deps frontend-mobile npm run --silent mobile:ota:keygen
.PHONY: mobile-ota-keygen

mobile-ota-bucket: ## create the public mobile OTA bucket in objectstorage
	@$(COMPOSE) up -d objectstorage --wait
	@$(COMPOSE_RUN) frontend-mobile npm run mobile:ota:bucket
.PHONY: mobile-ota-bucket

# Build the web bundle in the env-aware container (dist lands on the host via the
# bind mount), then zip + upload it and the channel manifest to the public
# bucket. Both steps run in the frontend toolchain: the OTA release is a
# frontend artifact, Django is not involved. VERSION defaults to the git-derived
# MOBILE_OTA_BUILD_ID (the hybrid <count>-<sha> id); override it to pin a specific
# release. CHANNEL defaults to the MOBILE_OTA_CHANNEL env var (frontend env files).
ota-publish: VERSION ?= $(MOBILE_OTA_BUILD_ID)
ota-publish: ## build and publish a mobile OTA bundle (VERSION defaults to <count>-<sha>, CHANNEL to MOBILE_OTA_CHANNEL)
	@$(COMPOSE) up -d objectstorage --wait
	@$(COMPOSE_RUN) frontend-mobile sh -c "npm run build && npm run mobile:ota:publish -- --version $(VERSION)$(if $(CHANNEL), --channel $(CHANNEL))"
.PHONY: ota-publish

# -- Linters

lint: ## run all linters
lint: \
  lint-back \
  lint-front \
  typecheck-front \
  lint-mta-in \
  lint-mta-in-py
.PHONY: lint

lint-check:  ## run all linters in check mode (no auto-fix)
lint-check: \
  lint-check-back \
  typecheck-front \
  lint-front
.PHONY: lint-check

lint-back: build-python-base ## run back-end linters (with auto-fix)
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

analyze-front: ## analyze frontend bundle sizes (per-chunk + per-package breakdown)
	@$(COMPOSE) run --rm frontend-tools npm run analyze
.PHONY: analyze-front

typecheck-front: ## run the frontend type checker
	@$(COMPOSE) run --rm frontend-tools npm run ts:check
.PHONY: typecheck-front

lint-front: ## run the frontend linter
	@$(COMPOSE) run --rm frontend-tools npm run lint
.PHONY: lint-front

lint-mta-in: ## lint mta-in python sources (Postfix milter implementation)
	$(COMPOSE_RUN) --rm -e EXEC_CMD_ONLY=true mta-in-test ruff format .
	#$(COMPOSE_RUN) --rm -e EXEC_CMD_ONLY=true mta-in-test ruff check . --fix
	#$(COMPOSE_RUN) --rm -e EXEC_CMD_ONLY=true mta-in-test pylint .
.PHONY: lint-mta-in

lint-mta-in-py: ## lint mta-in python sources (pure-Python pymta implementation)
	$(COMPOSE_RUN) --rm -e EXEC_CMD_ONLY=true mta-in-py-test ruff format .
	$(COMPOSE_RUN) --rm -e EXEC_CMD_ONLY=true mta-in-py-test ruff check . --fix
.PHONY: lint-mta-in-py


# -- Tests

test: ## run all tests
test: \
  test-back \
  test-front \
  test-mta-in \
  test-mta-in-py \
  test-mpa \
  test-socks-proxy
.PHONY: test

test-back: build-python-base ## run back-end tests
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	bin/pytest $${args:-${1}}
.PHONY: test-back

test-back-parallel: build-python-base ## run all back-end tests in parallel
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	bin/pytest -n auto $${args:-${1}}
.PHONY: test-back-parallel

fuzz-back: build-python-base ## run back-end fuzz tests
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	bin/pytest -m fuzz $${args:-${1}}
.PHONY: fuzz-back

fuzz-back-intensive: build-python-base ## run back-end fuzz tests with 10x more examples (~20-30 min)
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	rm -rf src/backend/.hypothesis/examples && \
	FUZZ_EXAMPLES=20000 bin/pytest -m fuzz $${args:-${1}}
.PHONY: fuzz-back-intensive

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

test-mta-in: build-python-base ## run the mta-in tests against the Postfix milter implementation
	@$(COMPOSE) run --build --rm mta-in-test
.PHONY: test-mta-in

test-mta-in-py: build-python-base ## run the mta-in tests against the pure-Python (aiosmtpd) implementation
	@$(COMPOSE) run --build --rm mta-in-py-test
.PHONY: test-mta-in-py


test-mpa: build-python-base ## run the mpa tests
	@$(COMPOSE) run --build --rm mpa-test
.PHONY: test-mpa

test-jmap-email: build-python-base ## run the jmap-email package tests (zero infrastructure deps)
	@$(COMPOSE) run --build --rm jmap-email-test
.PHONY: test-jmap-email

fuzz-jmap-email: build-python-base ## run the jmap-email Hypothesis fuzz suite
	@$(COMPOSE) run --build --rm jmap-email-test pytest -m fuzz tests/
.PHONY: fuzz-jmap-email

lint-jmap-email: build-python-base ## lint the jmap-email library (ruff check + format check + pylint)
	@$(COMPOSE) run --build --rm --entrypoint ruff jmap-email-test check jmap_email tests
	@$(COMPOSE) run --build --rm --entrypoint ruff jmap-email-test format --check jmap_email tests
	@$(COMPOSE) run --build --rm --entrypoint pylint jmap-email-test jmap_email tests
.PHONY: lint-jmap-email

format-jmap-email: build-python-base ## lint the jmap-email library (ruff check + format check + pylint)
	@$(COMPOSE) run --build --rm --entrypoint ruff jmap-email-test format jmap_email tests
.PHONY: format-jmap-email

typecheck-jmap-email: build-python-base ## type-check the jmap-email library with ty (Astral, Rust)
	@$(COMPOSE) run --build --rm --entrypoint ty jmap-email-test check
.PHONY: typecheck-jmap-email

release-jmap-email: ## publish jmap-email to PyPI (interactive: TestPyPI → smoke install → PyPI)
	@bin/release-jmap-email.sh
.PHONY: release-jmap-email

test-socks-proxy: build-python-base ## run the socks-proxy tests
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

test-e2e-ci: build-python-base ## Setup and run e2e tests in CI mode
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
	@$(COMPOSE_E2E) run --rm backend python manage.py create_bucket --storage message-blobs --expire-days 1
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

migrate: build-python-base ## run django migrations for the messages project.
	@echo "$(BOLD)Running migrations$(RESET)"
	@$(MANAGE_DB) migrate
.PHONY: migrate

showmigrations: ## show all migrations for the messages project.
	@$(MANAGE_DB) showmigrations
.PHONY: showmigrations

superuser: build-python-base ## Create an admin superuser with password "admin" and promote user1 as superuser
	@echo "$(BOLD)Creating a Django superuser$(RESET)"
	@$(MANAGE_DB) createsuperuser --email admin@admin.local --password admin
	@$(MANAGE_DB) createsuperuser --email user1@example.local --password user1
	@echo "$(BOLD)Creating the example.local autojoin domain$(RESET)"
	@$(MANAGE_DB) shell -c "from core.models import MailDomain; MailDomain.objects.get_or_create(name='example.local', defaults={'oidc_autojoin': True, 'identity_sync': True})"
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

deps-lock-back: build-python-base ## lock the dependencies
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

collectstatic: build-python-base ## collect static files
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

deploy/env/%.local:
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
install-front: ## install the frontend locally (freezes the lockfile, then runs the dependency guardrail)
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	$(COMPOSE) run --rm --build frontend-tools npm install $${args:-${1}}
	@$(COMPOSE) run --rm frontend-tools npm run check:deps
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

# Hybrid OTA/build version: a monotonic commit count (for ordering — enables a
# future downgrade check) plus the short SHA (for traceability). Computed on the
# HOST (git is not in the container) and injected; CI may override it.
MOBILE_OTA_BUILD_ID ?= $(shell git rev-list --count HEAD)-$(shell git rev-parse --short HEAD)

# Mobile (Capacitor). The web bundle is built in a container (frontend-mobile,
# which carries the env_file so the NEXT_PUBLIC_* vars are inlined) and synced
# into the native projects. The sync (not a bare copy) also regenerates the
# gitignored capacitor-cordova-android-plugins/ scaffolding that Gradle needs,
# so always run `make mobile-build` after a fresh checkout. The native compile /
# IDE / device steps are macOS- and SDK-bound, so they stay on the host.
# MOBILE_OTA_BUILD_ID is passed so `cap sync` stamps it as the builtin bundle version
# (capacitor.config.ts), letting the OTA freshness check match a same-commit
# manifest instead of re-downloading on first launch.
#
# Hot reload: MOBILE_DEV_SERVER_URL (frontend env files, set by default in dev)
# is baked as the WebView's server.url at `cap sync` — see docs/mobile.md.
mobile-build: ## build the web bundle and sync it + native plugins into the projects (container, env-aware)
	@$(COMPOSE) run --rm --build -e MOBILE_OTA_BUILD_ID=$(MOBILE_OTA_BUILD_ID) frontend-mobile npm run mobile:build
.PHONY: mobile-build

# Regenerate the native app icons and splashscreens from src/frontend/assets/
# (icon-only/icon-foreground/logo PNGs). Idempotent; run it after changing the
# source assets, then commit the regenerated android/ and ios/ resources.
mobile-assets: ## (re)generate native app icons & splashscreens (container)
	@$(COMPOSE) run --rm --build frontend-mobile npm run mobile:assets
.PHONY: mobile-assets

mobile-android: mobile-build ## build the bundle (container) then open the Android project in Android Studio (host)
	@if command -v studio.sh >/dev/null 2>&1; then studio.sh src/frontend/android; \
	elif command -v android-studio >/dev/null 2>&1; then android-studio src/frontend/android; \
	elif [ "$$(uname)" = "Darwin" ]; then open -a "Android Studio" src/frontend/android; \
	else echo "Android Studio introuvable : ouvre src/frontend/android manuellement." && exit 1; fi
.PHONY: mobile-android

mobile-ios: mobile-build ## build the bundle (container) then open the iOS project in Xcode (host, macOS)
	@open src/frontend/ios/App/App.xcodeproj
.PHONY: mobile-ios

# adb/gradlew drive the Android SDK and a USB-attached device, so — unlike the
# web build — they run on the HOST, not in a container (same as mobile-android
# above). We call them directly (never `npm run …`, which is container-only
# here), so this Makefile is the single source of truth for the port list and
# the gradle task.
# Ports the in-app WebView reaches through the device→host adb tunnel:
# 8900 dev frontend, 8901 backend, 8902 Keycloak, 8906 object storage (OTA).
ANDROID_REVERSE_PORTS = 8900 8901 8902 8906
ANDROID_DEBUG_APK = src/frontend/android/app/build/outputs/apk/debug/app-debug.apk

mobile-android-reverse: ## (host) map device ports to the dev stack via adb reverse
	@$(foreach port,$(ANDROID_REVERSE_PORTS),adb reverse tcp:$(port) tcp:$(port);)
.PHONY: mobile-android-reverse

# Read a MOBILE_* value from the frontend env files the way the container does
# (frontend.local overriding frontend.defaults, last definition wins). Gradle
# runs on the host and would otherwise read a *different* MOBILE_APP_ID than the
# `cap sync` that produced the bundle — a divergence a store upload freezes
# forever. A gradle guard cross-checks the two (android/app/build.gradle).
# Surrounding double quotes are stripped the way compose's dotenv parser does,
# so a quoted value does not reach gradle with its quotes and trip the appId
# cross-check with an unreadable message.
mobile_env = $(shell sed -n 's/^$(1)=//p' deploy/env/frontend.defaults deploy/env/frontend.local 2>/dev/null | tail -n1 | sed 's/^"//;s/"$$//')

# The same env the container used for `cap sync` must reach the host gradle
# build, debug included: a MOBILE_AUTH_SCHEME set only on the JS side would ship
# a manifest declaring the old scheme, and the OIDC callback would never come
# back — the login opens, and nothing returns.
mobile-android-run: mobile-build ## (host) build+install the debug APK on a device then adb reverse
	@cd src/frontend/android && \
		MOBILE_APP_ID="$(call mobile_env,MOBILE_APP_ID)" \
		MOBILE_APP_NAME="$(call mobile_env,MOBILE_APP_NAME)" \
		MOBILE_AUTH_SCHEME="$(call mobile_env,MOBILE_AUTH_SCHEME)" \
		./gradlew assembleDebug
	@adb install -r $(ANDROID_DEBUG_APK)
	@$(MAKE) mobile-android-reverse
.PHONY: mobile-android-run

i18n-generate-front: ## Extract the frontend translation inside a json to be used for crowdin
	@$(COMPOSE) run --rm --build frontend-tools npm run i18n:extract
.PHONY: i18n-generate-front

api-update-back: build-python-base ## Update the OpenAPI schema
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

build-keycloak: ## Build the custom Keycloak provider JARs (writes JAR alongside pom.xml so it can be committed)
	@docker volume create st-messages-keycloak-mvn-cache >/dev/null
	@docker run --rm \
		-v "$(PWD)/src/keycloak/bulk-role-membership":/build \
		-v st-messages-keycloak-mvn-cache:/root/.m2 \
		-w /build \
		maven:3.9-eclipse-temurin-21 \
		mvn -B -q -o package -DskipTests 2>/dev/null \
		|| docker run --rm \
			-v "$(PWD)/src/keycloak/bulk-role-membership":/build \
			-v st-messages-keycloak-mvn-cache:/root/.m2 \
			-w /build \
			maven:3.9-eclipse-temurin-21 \
			mvn -B -q package -DskipTests
	@cp src/keycloak/bulk-role-membership/target/bulk-role-membership.jar \
		src/keycloak/bulk-role-membership/bulk-role-membership.jar
.PHONY: build-keycloak

test-keycloak: ## run all Keycloak provider tests (builds JARs, brings up Keycloak)
	@bin/test-keycloak
.PHONY: test-keycloak

deps-lock-mta-in: build-python-base ## lock the dependencies for mta-in (shared between both implementations)
	@$(COMPOSE) run --rm --build mta-in-uv uv lock
.PHONY: deps-lock-mta-in

