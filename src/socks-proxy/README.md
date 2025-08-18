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

The service requires the `PROXY_USERS` environment variable:

```bash
PROXY_USERS=user1:password1,user2:password2
```

**Format**: Comma-separated list of `username:password` pairs

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
