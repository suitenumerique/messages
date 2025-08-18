# SOCKS Proxy Service

A high-performance SOCKS5 proxy server built with Dante, designed for secure network tunneling and testing environments.

## Overview

This service provides a SOCKS5 proxy server that can be used for routing SMTP traffic through a specifi IP address.

## Architecture

### Components

- **Dante SOCKS Server** - Custom-built from source (v1.4.4)
- **User Authentication** - Username/password based authentication
- **Docker Containerization** - Multi-stage build for optimized runtime
- **Comprehensive Testing** - Full test suite with mock SMTP server

## Configuration

### Environment Variables

|Variable|Description|Required|Default|
|---|---|---|---|
| PROXY_USERS | List of username:password pairs allowed to connect in the format "user1:pass1,user2:pass2". | true | |
| PROXY_EXTERNAL | The outbound connections IP address or interface name. | true | |
| PROXY_INTERNAL | The inbound connections IP addresses or interfaces names.  | false | "0.0.0.0" |
| PROXY_INTERNAL_PORT | The inbound connections TCP port to listen to. | false | "1080" |
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
