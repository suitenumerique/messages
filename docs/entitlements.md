# Entitlements System

The entitlements system provides a pluggable backend architecture for checking user access rights and synchronizing mail domain admin permissions. It integrates with the DeployCenter (Espace Operateur) API in production and uses a local backend for development.

## Architecture

```text
┌─────────────────────────────────────────────┐
│   OIDC Authentication Backend               │
│   _sync_entitlements() on every login       │
└──────────────┬──────────────────────────────┘
               │
┌──────────────▼──────────────────────────────┐
│   Service Layer                             │
│   get_user_entitlements()                   │
└──────────────┬──────────────────────────────┘
               │
┌──────────────▼──────────────────────────────┐
│   Backend Factory (singleton)               │
│   get_entitlements_backend()                │
└──────────────┬──────────────────────────────┘
               │
       ┌───────┴───────┐
       │               │
┌──────▼─────┐  ┌──────▼───────────────┐
│   Local    │  │   DeployCenter       │
│  Backend   │  │   Backend            │
│ (dev/test) │  │ (production, cached) │
└────────────┘  └──────────────────────┘
```

### Components

- **Service layer** (`core/entitlements/__init__.py`): Public `get_user_entitlements()` function and `EntitlementsUnavailableError` exception.
- **Backend factory** (`core/entitlements/factory.py`): `@functools.cache` singleton that imports and instantiates the configured backend class.
- **Abstract base** (`core/entitlements/backends/base.py`): Defines the `EntitlementsBackend` interface.
- **Local backend** (`core/entitlements/backends/local.py`): Always grants access, returns `None` for `can_admin_maildomains` (disabling sync).
- **DeployCenter backend** (`core/entitlements/backends/deploycenter.py`): Calls the DeployCenter API with internal Django cache.
- **OIDC sync** (`core/authentication/backends.py`): Syncs `MailDomainAccess` ADMIN records on every login based on `can_admin_maildomains`.

### Error Handling

- **Login sync is fail-open**: if the entitlements service is unavailable during OIDC login, existing `MailDomainAccess` records are preserved and the user is allowed in.
- The DeployCenter backend falls back to stale cached data when the API is unavailable.
- `EntitlementsUnavailableError` is only raised when the API fails and no cache exists.

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ENTITLEMENTS_BACKEND` | `core.entitlements.backends.local.LocalEntitlementsBackend` | Python import path of the backend class |
| `ENTITLEMENTS_BACKEND_PARAMETERS` | `{}` | JSON object of parameters passed to the backend constructor |
| `ENTITLEMENTS_CACHE_TIMEOUT` | `300` | Cache TTL in seconds |

### DeployCenter Backend Parameters

When using `core.entitlements.backends.deploycenter.DeployCenterEntitlementsBackend`, provide these in `ENTITLEMENTS_BACKEND_PARAMETERS`:

```json
{
  "base_url": "https://deploycenter.example.com/api/v1.0/entitlements/",
  "service_id": "42",
  "api_key": "your-api-key",
  "timeout": 10,
  "oidc_claims": ["siret"]
}
```

| Parameter | Required | Description |
|---|---|---|
| `base_url` | Yes | Full URL of the DeployCenter entitlements endpoint |
| `service_id` | Yes | Service identifier in DeployCenter |
| `api_key` | Yes | API key for `X-Service-Auth` header |
| `timeout` | No | HTTP timeout in seconds (default: 10) |
| `oidc_claims` | No | List of OIDC claim names to extract from user_info and forward as query params (e.g. `["siret"]`) |

### Example Production Configuration

```bash
ENTITLEMENTS_BACKEND=core.entitlements.backends.deploycenter.DeployCenterEntitlementsBackend
ENTITLEMENTS_BACKEND_PARAMETERS='{"base_url":"https://deploycenter.example.com/api/v1.0/entitlements/","service_id":"42","api_key":"secret-key","timeout":10,"oidc_claims":["siret"]}'
ENTITLEMENTS_CACHE_TIMEOUT=300
```

## Backend Interface

Custom backends must extend `EntitlementsBackend` and implement:

```python
class MyBackend(EntitlementsBackend):
    def __init__(self, **kwargs):
        # Receive ENTITLEMENTS_BACKEND_PARAMETERS as kwargs
        pass

    def get_user_entitlements(self, user_sub, user_email, user_info=None, force_refresh=False):
        # Return: {"can_access": bool, "can_admin_maildomains": list[str] | None}
        # Return None for can_admin_maildomains to skip domain admin sync.
        # Raise EntitlementsUnavailableError on failure.
        pass
```

## DeployCenter API

The DeployCenter backend calls:

```text
GET {base_url}?service_id=X&account_type=user&account_email=X&siret=X
```

Headers:
- `X-Service-Auth: Bearer {api_key}`

Query parameters include any configured `oidc_claims` extracted from the OIDC user_info response (e.g. `siret`).

Response: `{"entitlements": {"can_access": bool, "can_admin_maildomains": [str], ...}}`

## OIDC Login Integration

During OIDC login (`post_get_or_create_user`), the system:

1. Calls `get_user_entitlements` with `force_refresh=True` (resets cache)
2. Syncs `MailDomainAccess` ADMIN records based on `can_admin_maildomains`:
   - Compares entitled domains with existing records (optimistic early return if in sync)
   - Creates missing admin accesses for entitled domains (using `update_or_create`)
   - Removes admin accesses for domains not in the entitled list
3. If `can_admin_maildomains` is `None` (e.g. local backend), sync is skipped entirely
4. If the entitlements service is unavailable, existing accesses are preserved (fail-open)

### Caching Behavior

- The DeployCenter backend caches entitlements in Django's cache framework (TTL: `ENTITLEMENTS_CACHE_TIMEOUT`).
- On login, `force_refresh=True` bypasses the cache to fetch fresh data.
- If the API fails during a forced refresh, stale cached data is returned as fallback.
- Logging out and back in triggers a fresh fetch, effectively resetting the cache for that user.

### Deployment Consideration

Before enabling the DeployCenter backend in production, ensure that existing domain admin assignments are present in DeployCenter. The entitlements sync will **remove** admin accesses that are not in the DeployCenter response.
