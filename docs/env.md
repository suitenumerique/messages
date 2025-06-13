# Environment Variables

This document provides a comprehensive overview of all environment variables used in the Messages application. These variables are organized by service and functionality.

## Core Application Configuration

### Django Settings

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `DJANGO_CONFIGURATION` | `Development` | Django configuration class to use (Development, Production, Test, etc.) | Required |
| `DJANGO_SECRET_KEY` | None | Secret key for cryptographic signing | Required |
| `DJANGO_ALLOWED_HOSTS` | `[]` | List of allowed hostnames | Required |
| `DJANGO_SETTINGS_MODULE` | `messages.settings` | Django settings module | Required |
| `DJANGO_SUPERUSER_PASSWORD` | `admin` | Default superuser password for development | Dev |
| `DJANGO_DATA_DIR` | `/data` | Base directory for data storage | Optional |

### Database Configuration

#### PostgreSQL (Main Database)
| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `DATABASE_URL` | None | Complete database URL (overrides individual DB_* vars) | Optional |
| `DB_ENGINE` | `django.db.backends.postgresql_psycopg2` | Database engine | Optional |
| `DB_HOST` | `localhost` | Database hostname | Optional |
| `DB_NAME` | `messages` | Database name | Optional |
| `DB_USER` | `dbuser` | Database username | Optional |
| `DB_PASSWORD` | `dbpass` | Database password | Optional |
| `DB_PORT` | `5432` | Database port | Optional |

#### PostgreSQL (Keycloak)
| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `POSTGRES_DB` | `keycloak` | Keycloak database name | Dev |
| `POSTGRES_USER` | `user` | Keycloak database user | Dev |
| `POSTGRES_PASSWORD` | `pass` | Keycloak database password | Dev |

### Redis Configuration

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `REDIS_URL` | `redis://redis:6379` | Redis connection URL | Optional |
| `CELERY_BROKER_URL` | `redis://redis:6379` | Celery message broker URL | Optional |
| `CACHES_DEFAULT_TIMEOUT` | `30` | Default cache timeout in seconds | Optional |

### Elasticsearch Configuration

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `ELASTICSEARCH_URL` | `["http://elasticsearch:9200"]` | Elasticsearch hosts list | Optional |
| `ELASTICSEARCH_TIMEOUT` | `20` | Elasticsearch query timeout | Optional |
| `ELASTICSEARCH_INDEX_THREADS` | `True` | Enable thread indexing | Optional |

## Mail Processing Configuration

### MTA Settings

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `MTA_OUT_HOST` | None | Outbound SMTP server host | Required |
| `MTA_OUT_SMTP_USERNAME` | None | Outbound SMTP username | Optional |
| `MTA_OUT_SMTP_PASSWORD` | None | Outbound SMTP password | Optional |
| `MTA_OUT_SMTP_USE_TLS` | `True` | Use TLS for outbound SMTP | Optional |
| `MDA_API_SECRET` | `default-mda-api-secret` | Shared secret for MDA API | Required |
| `MDA_API_BASE_URL` | None | Base URL for MDA API | Dev |

### MTA-Out Specific
| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `SMTP_RELAY_HOST` | None | SMTP relay server | Dev |
| `SMTP_USERNAME` | None | SMTP authentication username | Dev |
| `SMTP_PASSWORD` | None | SMTP authentication password | Dev |

### Email Domain Configuration

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `MESSAGES_TESTDOMAIN` | `localhost` | Test domain for development | Dev |
| `MESSAGES_TESTDOMAIN_MAPPING_BASEDOMAIN` | `gouv.fr` | Base domain mapping | Dev |
| `MESSAGES_ACCEPT_ALL_EMAILS` | `False` | Accept emails to any domain | Optional |

### DKIM Configuration

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `MESSAGES_DKIM_SELECTOR` | `default` | DKIM selector | Optional |
| `MESSAGES_DKIM_DOMAINS` | `[]` | List of domains for DKIM signing | Optional |
| `MESSAGES_DKIM_PRIVATE_KEY_B64` | None | Base64 encoded DKIM private key | Optional |
| `MESSAGES_DKIM_PRIVATE_KEY_FILE` | None | Path to DKIM private key file | Optional |

## Storage Configuration

### S3-Compatible Storage

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `AWS_S3_ENDPOINT_URL` | None | S3 endpoint URL | Optional |
| `AWS_S3_ACCESS_KEY_ID` | None | S3 access key | Optional |
| `AWS_S3_SECRET_ACCESS_KEY` | None | S3 secret key | Optional |
| `AWS_S3_REGION_NAME` | None | S3 region | Optional |
| `AWS_STORAGE_BUCKET_NAME` | `st-messages-media-storage` | S3 bucket name | Optional |
| `AWS_S3_UPLOAD_POLICY_EXPIRATION` | `86400` | Upload policy expiration (24h) | Optional |
| `MEDIA_BASE_URL` | None | Base URL for media files | Optional |
| `ITEM_FILE_MAX_SIZE` | `5368709120` | Max file size (5GB) | Optional |

### Static Files

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `STORAGES_STATICFILES_BACKEND` | `whitenoise.storage.CompressedManifestStaticFilesStorage` | Static files storage backend | Optional |

## Authentication & Authorization

### OIDC Configuration

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `OIDC_CREATE_USER` | `False` | Automatically create users from OIDC | Optional |
| `OIDC_RP_CLIENT_ID` | `st_messages` | OIDC client ID | Required |
| `OIDC_RP_CLIENT_SECRET` | None | OIDC client secret | Required |
| `OIDC_RP_SIGN_ALGO` | `RS256` | OIDC signing algorithm | Optional |
| `OIDC_RP_SCOPES` | `openid email` | OIDC scopes | Optional |
| `OIDC_OP_JWKS_ENDPOINT` | None | OIDC JWKS endpoint | Required |
| `OIDC_OP_AUTHORIZATION_ENDPOINT` | None | OIDC authorization endpoint | Required |
| `OIDC_OP_TOKEN_ENDPOINT` | None | OIDC token endpoint | Required |
| `OIDC_OP_USER_ENDPOINT` | None | OIDC user info endpoint | Required |
| `OIDC_OP_LOGOUT_ENDPOINT` | None | OIDC logout endpoint | Optional |

### OIDC Advanced Settings

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `OIDC_USE_NONCE` | `True` | Use nonce in OIDC flow | Optional |
| `OIDC_REDIRECT_REQUIRE_HTTPS` | `False` | Require HTTPS for redirects | Optional |
| `OIDC_REDIRECT_ALLOWED_HOSTS` | `[]` | Allowed redirect hosts | Optional |
| `OIDC_STORE_ID_TOKEN` | `True` | Store ID token | Optional |
| `OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION` | `True` | Use email as fallback identifier | Optional |
| `OIDC_ALLOW_DUPLICATE_EMAILS` | `False` | Allow duplicate emails (⚠️ Security risk) | Optional |
| `OIDC_AUTH_REQUEST_EXTRA_PARAMS` | `{}` | Extra parameters for auth requests | Optional |

### Authentication URLs

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `LOGIN_REDIRECT_URL` | None | Post-login redirect URL | Optional |
| `LOGIN_REDIRECT_URL_FAILURE` | None | Login failure redirect URL | Optional |
| `LOGOUT_REDIRECT_URL` | None | Post-logout redirect URL | Optional |
| `ALLOW_LOGOUT_GET_METHOD` | `True` | Allow GET method for logout | Optional |

### User Mapping

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `USER_OIDC_ESSENTIAL_CLAIMS` | `[]` | Essential OIDC claims | Optional |
| `USER_OIDC_FIELDS_TO_FULLNAME` | `["first_name", "last_name"]` | Fields for full name | Optional |
| `USER_OIDC_FIELD_TO_SHORTNAME` | `first_name` | Field for short name | Optional |

## Security & CORS

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `CORS_ALLOW_ALL_ORIGINS` | `False` | Allow all CORS origins | Optional |
| `CORS_ALLOWED_ORIGINS` | `[]` | Specific allowed CORS origins | Optional |
| `CORS_ALLOWED_ORIGIN_REGEXES` | `[]` | Regex patterns for allowed origins | Optional |
| `CSRF_TRUSTED_ORIGINS` | `[]` | Trusted origins for CSRF | Optional |
| `SERVER_TO_SERVER_API_TOKENS` | `[]` | API tokens for server-to-server auth | Optional |

## Monitoring & Observability

### Sentry

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `SENTRY_DSN` | None | Sentry DSN for error tracking | Optional |

### PostHog

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `POSTHOG_KEY` | None | PostHog analytics key | Optional |

### Logging

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `LOGGING_LEVEL_LOGGERS_ROOT` | `INFO` | Root logger level | Optional |
| `LOGGING_LEVEL_LOGGERS_APP` | `INFO` | Application logger level | Optional |
| `LOGGING_LEVEL_HANDLERS_CONSOLE` | `INFO` | Console handler level | Optional |

## API Configuration

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `API_USERS_LIST_LIMIT` | `5` | Default limit for user list API | Optional |
| `API_USERS_LIST_THROTTLE_RATE_SUSTAINED` | `180/hour` | Sustained throttle rate | Optional |
| `API_USERS_LIST_THROTTLE_RATE_BURST` | `30/minute` | Burst throttle rate | Optional |

### OpenAPI Schema

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `SPECTACULAR_SETTINGS_ENABLE_DJANGO_DEPLOY_CHECK` | `False` | Enable deploy check in OpenAPI | Optional |

## Frontend Configuration

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `FRONTEND_THEME` | None | Frontend theme identifier | Optional |
| `NEXT_PUBLIC_API_ORIGIN` | None | Frontend API origin | Dev |
| `NEXT_PUBLIC_S3_DOMAIN_REPLACE` | None | S3 domain replacement for frontend | Dev |

## Development Tools

### Crowdin (Translations)

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `CROWDIN_PERSONAL_TOKEN` | None | Crowdin API token | Dev |
| `CROWDIN_PROJECT_ID` | None | Crowdin project ID | Dev |
| `CROWDIN_BASE_PATH` | `/app/src` | Base path for translations | Dev |

## Application Settings

### Business Logic

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `TRASHBIN_CUTOFF_DAYS` | `30` | Days before permanent deletion | Optional |
| `INVITATION_VALIDITY_DURATION` | `604800` | Invitation validity (7 days) | Optional |

### Internationalization

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `LANGUAGE_CODE` | `en-us` | Default language code | Optional |

## Legend

- **Required**: Must be set for the application to function
- **Dev**: Required for development/testing environments
- **Optional**: Has sensible defaults, can be customized

## Environment Files

The application uses environment files located in `env.d/development/` for different services:

- `backend.dist` - Main Django application settings
- `common.dist` - Shared settings across services
- `postgresql.dist` - PostgreSQL database configuration
- `kc_postgresql.dist` - Keycloak database configuration
- `mta-in.dist` - Inbound mail server settings
- `mta-out.dist` - Outbound mail server settings
- `crowdin.dist` - Translation service configuration

## Security Notes

⚠️ **Important Security Considerations:**

1. **Never commit actual secrets** - Use `.dist` files as templates
2. **OIDC_ALLOW_DUPLICATE_EMAILS** - Should remain `False` in production
3. **CORS_ALLOW_ALL_ORIGINS** - Should be `False` in production
4. **DJANGO_SECRET_KEY** - Must be unique and secret in production
5. **Database passwords** - Use strong, unique passwords
6. **API tokens** - Rotate regularly and keep secure

## Production Deployment

For production deployments, ensure:

1. All **Required** variables are properly configured
2. Secrets are managed through secure secret management systems
3. HTTPS is enforced for all external communications
4. Database connections use SSL/TLS
5. File storage uses appropriate access controls