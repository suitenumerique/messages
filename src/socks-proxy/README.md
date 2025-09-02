# SOCKS Proxy Service

A high-performance SOCKS5 proxy server built with Dante, designed for secure network tunneling and testing environments.

## Overview

This service provides a SOCKS5 proxy server that can be used for routing SMTP traffic through a specific IP address.

## Architecture

### Components

- **Dante SOCKS Server** — Built from source (v1.4.4)
- **Authentication** — Username/password (RFC 1929) over SOCKS5 (RFC 1928)
- **Dockerized** — Multi-stage build for a minimal runtime image
- **Tests** — Full suite with a mock SMTP server

## Configuration

### Environment Variables

|Variable|Description|Required|Default|
|---|---|---|---|
| PROXY_USERS | Comma‑separated username:password pairs allowed to connect (e.g., "user1:pass1,user2:pass2"). | true | |
| PROXY_EXTERNAL | Outbound interface name or IP address. | false | "eth0" |
| PROXY_INTERNAL | Inbound bind interface name or IP address. | false | "0.0.0.0" |
| PROXY_INTERNAL_PORT | Inbound TCP port to listen on. | false | "1080" |
| PROXY_DEBUG_LEVEL | The debug level. | false | "0" |
| PROXY_SOURCE_IP_WHITELIST | The source IPs allowed to connect to the proxy. Be aware you have to use `network_mode: host` for this feature to work. | false | "0.0.0.0/0" |

## Testing

### Test Suite Features

- **Authentication Testing** - Valid/invalid credentials, missing auth
- **Connection Testing** - Establishment, timeouts, connection refused
- **SMTP via Proxy** - Email delivery through SOCKS proxy
- **Connection Info Capture** - IP address logging for proxy verification

### Running Tests

```bash
# Run this at the root of Messages:
make socks-proxy-test
```
