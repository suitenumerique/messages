# Messages E2E Tests

End-to-end tests for the Messages application using Playwright.

## Prerequisites

- Docker and Docker Compose installed
- Messages project configured

## Running the tests

### In headless mode (CI)

```bash
make test-e2e
```

### In UI mode

```bash
make test-e2e-ui
```

Open the Playwright UI on http://localhost:8932 to write and debug the tests interactively.

### In Dev mode

Start playwright in UI Mode and use the dev frontend service to avoid rebuilding
 the frontend after each change.
```bash
make test-e2e-dev
```

Open the Playwright UI on http://localhost:8932 to write and debug the tests interactively.

## Explanation

### Isolated services

E2E tests use [dedicated services](./compose.yaml) especially for the database and the object storage.

### Caddy to serve the frontend and the backend

Caddy is used as a reverse proxy to serve the frontend and the backend on the same origin, avoiding cross-origin cookie issues.

### Environment variables

E2E configuration files are located in `env.d/development/*.e2e`:
- `backend.e2e`: Backend configuration for tests
- `frontend.e2e`: Frontend configuration for tests
- `keycloak.e2e`: Keycloak configuration for tests

