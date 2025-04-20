# ST Messages MTA outbound

The MTA outbound service is in charge of sending emails to the Internet from the MDA.

It only deals with outbound email and is optimized specifically for this purpose. It doesn't handle any local mail delivery.

This MTA container is based on Postfix with a simplified configuration focused solely on outbound mail delivery. It's entirely stateless and configurable via environment variables. By default, it delivers email directly to recipient servers via DNS MX lookups. Optionally, it can be configured to relay through an upstream SMTP server.

It is battle-tested with a complete Python test suite.

## Key features
- Secure SMTP on port 587 with TLS required for authentication
- Simple authentication for incoming clients (`SMTP_USERNAME`/`SMTP_PASSWORD`)
- Direct outbound email delivery via DNS MX lookups (default)
- Optional relaying via a configured upstream SMTP server (`RELAYHOST`)
- Optional authentication to the upstream relay server (`SMTP_RELAY_USERNAME`/`SMTP_RELAY_PASSWORD`)
- No local mail handling or unnecessary components

## Usage

The outbound MTA accepts connections on port 587. TLS is required for authentication.
Configuration is done entirely through environment variables:

### Required Environment Variables (for authenticating clients TO this service)
- `SMTP_USERNAME`: Username clients must use to authenticate to this service.
- `SMTP_PASSWORD`: Password clients must use to authenticate to this service.

### Optional Environment Variables
- `RELAYHOST`: Upstream SMTP server to relay mail through (e.g., `[smtp.test-server.com]:1025`). If unset (default), mail is delivered directly via DNS MX lookups.
- `SMTP_RELAY_USERNAME`: Username for this service to use when authenticating to the `RELAYHOST` (only used if `RELAYHOST` is set).
- `SMTP_RELAY_PASSWORD`: Password for this service to use when authenticating to the `RELAYHOST` (only used if `RELAYHOST` is set).
- `MYHOSTNAME`: The hostname this MTA identifies itself with in HELO/EHLO commands (default: `localhost`). Setting a proper FQDN is recommended.
- `TLS_CERT_PATH`: Path to the TLS certificate file (default: `/etc/ssl/certs/ssl-cert-snakeoil.pem`). **WARNING:** Mount a real certificate in production.
- `TLS_KEY_PATH`: Path to the TLS private key file (default: `/etc/ssl/private/ssl-cert-snakeoil.key`). **WARNING:** Mount a real key in production.
- `MESSAGE_SIZE_LIMIT`: Maximum size of messages in bytes (default: `10240000`).

## Testing

The service includes a comprehensive test suite that verifies:
- Authentication functionality (for incoming clients)
- Email sending capabilities (both direct and via relayhost, with and without relay auth)
- Support for different email formats and attachments 