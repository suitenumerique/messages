# Entitlements System

The entitlements system provides a pluggable backend architecture for checking user access rights and mailbox storage quotas. It integrates with the DeployCenter (Espace Operateur) API in production and uses a dummy backend for development.

## Architecture

```
┌─────────────────────────────────────────────┐
│           Django Cache Layer                 │
│  get_user_entitlements()                     │
│  get_mailbox_entitlements()                  │
└──────────────┬──────────────────────────────┘
               │
┌──────────────▼──────────────────────────────┐
│         Backend Factory (singleton)          │
│  get_entitlements_backend()                  │
└──────────────┬──────────────────────────────┘
               │
       ┌───────┴───────┐
       │               │
┌──────▼─────┐  ┌──────▼───────────────┐
│   Dummy    │  │   DeployCenter       │
│  Backend   │  │   Backend            │
│ (dev/test) │  │  (production)        │
└────────────┘  └──────────────────────┘
```

### Components

- **Cached service layer** (`core/entitlements/__init__.py`): Public functions with Django cache. TTL configurable via `ENTITLEMENTS_CACHE_TIMEOUT`.
- **Backend factory** (`core/entitlements/factory.py`): `@functools.cache` singleton that imports and instantiates the configured backend class.
- **Abstract base** (`core/entitlements/backends/base.py`): Defines the `EntitlementsBackend` interface.
- **Dummy backend** (`core/entitlements/backends/dummy.py`): Always grants access, returns no storage info.
- **DeployCenter backend** (`core/entitlements/backends/deploycenter.py`): Calls the DeployCenter API.
- **OIDC access check** (`core/authentication/backends.py`): Enforces `can_access` at login time.
- **API endpoint** (`core/api/viewsets/entitlements.py`): `GET /api/v1.0/entitlements/` for the frontend.

### Error Handling

All backend methods raise `EntitlementsUnavailableError` on failure. The access check at login is **fail-open**: if the entitlements service is unavailable during OIDC login, the user is allowed in. The entitlements API endpoint returns 503 if the service is unavailable.

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ENTITLEMENTS_BACKEND` | `core.entitlements.backends.dummy.DummyEntitlementsBackend` | Python import path of the backend class |
| `ENTITLEMENTS_BACKEND_PARAMETERS` | `{}` | JSON object of parameters passed to the backend constructor |
| `ENTITLEMENTS_CACHE_TIMEOUT` | `300` | Cache TTL in seconds |

### DeployCenter Backend Parameters

When using `core.entitlements.backends.deploycenter.DeployCenterEntitlementsBackend`, provide these in `ENTITLEMENTS_BACKEND_PARAMETERS`:

```json
{
  "base_url": "https://deploycenter.example.com",
  "service_id": "messages-prod",
  "api_key": "your-api-key",
  "timeout": 10
}
```

### Example Production Configuration

```bash
ENTITLEMENTS_BACKEND=core.entitlements.backends.deploycenter.DeployCenterEntitlementsBackend
ENTITLEMENTS_BACKEND_PARAMETERS={"base_url":"https://deploycenter.example.com","service_id":"messages-prod","api_key":"secret-key","timeout":10}
ENTITLEMENTS_CACHE_TIMEOUT=300
```

## Backend Interface

Custom backends must extend `EntitlementsBackend` and implement:

```python
class MyBackend(EntitlementsBackend):
    def __init__(self, **kwargs):
        # Receive ENTITLEMENTS_BACKEND_PARAMETERS as kwargs
        pass

    def get_user_entitlements(self, user_sub, user_email, access_token=None):
        # Return: {"can_access": bool, "can_admin_maildomains": [str], "operator": dict|None}
        # Raise EntitlementsUnavailableError on failure
        pass

    def get_mailbox_entitlements(self, mailbox_email, access_token=None):
        # Return: {"max_storage": int|None, "storage_used": int|None}
        # Raise EntitlementsUnavailableError on failure
        pass
```

## DeployCenter API

The DeployCenter backend calls:

```
GET {base_url}/api/v1.0/entitlements?service_id=X&account_type=X&account_id=X
```

Headers:
- `X-Service-Auth: Bearer {api_key}`
- `Authorization: Bearer {access_token}` (if provided)

### User Entitlements Request

- `account_type=user`
- `account_id=<user_email>`

Response: `{"operator": {...}, "entitlements": {"can_access": bool, "can_admin_maildomains": [str], ...}}`

### Mailbox Entitlements Request

- `account_type=mailbox`
- `account_id=<mailbox_email>`

Response: `{"operator": {...}, "entitlements": {"max_storage": int, "storage_used": int, ...}}`

## OIDC Login Integration

During OIDC login (`post_get_or_create_user`), the system:

1. Fetches user entitlements with `force_refresh=True`
2. Syncs `MailDomainAccess` ADMIN records based on `can_admin_maildomains`:
   - Creates missing admin accesses for entitled domains
   - Removes admin accesses for domains not in the entitled list
3. If `can_admin_maildomains` is `None` (e.g. dummy backend), sync is skipped entirely
4. Checks `can_access` and denies login if `False` (raises `SuspiciousOperation`)
   - If the entitlements service is unavailable, login is allowed (fail-open)

### Deployment Consideration

Before enabling the DeployCenter backend in production, ensure that existing domain admin assignments are synced in DeployCenter. The entitlements sync will **remove** admin accesses that are not in the DeployCenter response.

## Frontend Quota Widget

The frontend includes a quota widget that displays mailbox storage usage in the header. It:

- Calls `GET /api/v1.0/entitlements/?mailbox_id=<uuid>` when a mailbox is selected
- Displays a progress bar with `storage_used / max_storage`
- Hides itself when no storage data is available (dummy backend)
- Caches data for 5 minutes on the client side
