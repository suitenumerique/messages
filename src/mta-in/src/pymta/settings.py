"""Environment-variable-driven settings for the pymta server."""

import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw


# ---------------------------------------------------------------------------
# MDA back-end (shared with the Postfix milter)
# ---------------------------------------------------------------------------

MDA_API_BASE_URL = _env_str("MDA_API_BASE_URL", "http://localhost:8000/api/v1.0/")
MDA_API_SECRET = _env_str("MDA_API_SECRET", "")
MDA_API_TIMEOUT = _env_int("MDA_API_TIMEOUT", 30)


# ---------------------------------------------------------------------------
# SMTP listener
# ---------------------------------------------------------------------------

PYMTA_SMTP_HOST = _env_str("PYMTA_SMTP_HOST", "0.0.0.0")  # noqa: S104
PYMTA_SMTP_PORT = _env_int("PYMTA_SMTP_PORT", 25)

# Banner / Received-header hostname. Matches Postfix's `myhostname`.
PYMTA_HOSTNAME = _env_str("PYMTA_HOSTNAME", _env_str("MYHOSTNAME", "mta-in"))

# ESMTP banner ident (after the hostname). Kept short and version-less so we
# don't broadcast "aiosmtpd X.Y.Z" to internet scanners.
PYMTA_IDENT = _env_str("PYMTA_IDENT", "ESMTP")


# ---------------------------------------------------------------------------
# Message-shape limits (security-critical)
# ---------------------------------------------------------------------------

# Total RFC822 message size cap. Mirrors Postfix `message_size_limit`.
MAX_INCOMING_EMAIL_SIZE = _env_int("MAX_INCOMING_EMAIL_SIZE", 10_240_000)

# RCPT TO per SMTP transaction. Mirrors Postfix `smtpd_recipient_limit=100`.
PYMTA_MAX_RECIPIENTS = _env_int("PYMTA_MAX_RECIPIENTS", 100)

# Envelopes per TCP connection (one envelope = MAIL FROM..DATA cycle).
PYMTA_MAX_ENVELOPES_PER_CONNECTION = _env_int("PYMTA_MAX_ENVELOPES_PER_CONNECTION", 10)

# RFC 5321 §4.5.3.1.1/.1.2: local-part ≤ 64 octets, domain ≤ 255 octets.
PYMTA_MAX_LOCAL_PART = _env_int("PYMTA_MAX_LOCAL_PART", 64)
PYMTA_MAX_DOMAIN = _env_int("PYMTA_MAX_DOMAIN", 255)


# ---------------------------------------------------------------------------
# Timeouts & connection caps
# ---------------------------------------------------------------------------

# Per-command idle timeout (seconds). Postfix default is 300 s; we tighten.
PYMTA_COMMAND_TIMEOUT = _env_int("PYMTA_COMMAND_TIMEOUT", 120)

# Total deadline for the DATA phase (seconds), wrapping the bytes-receive loop
# plus the MDA delivery call. Defends against slowloris on the body.
PYMTA_DATA_TIMEOUT = _env_int("PYMTA_DATA_TIMEOUT", 600)

# Per-IP concurrent SMTP sessions. 0 disables the cap.
PYMTA_MAX_SESSIONS_PER_IP = _env_int("PYMTA_MAX_SESSIONS_PER_IP", 100)

# Process-wide concurrent SMTP sessions. 0 disables.
PYMTA_MAX_SESSIONS_TOTAL = _env_int("PYMTA_MAX_SESSIONS_TOTAL", 1000)

# Per-session soft-error budget. Mirrors Postfix `smtpd_hard_error_limit`:
# once a session accumulates this many 4xx/5xx replies (typically over-limit
# or unknown-recipient RCPTs), the next misbehaviour gets a 421 and the
# connection closes. Defends against bulk address enumeration that lives in
# one TCP session.
PYMTA_HARD_ERROR_LIMIT = _env_int("PYMTA_HARD_ERROR_LIMIT", 50)


# ---------------------------------------------------------------------------
# ESMTP feature toggles
# ---------------------------------------------------------------------------

PYMTA_ENABLE_SMTPUTF8 = _env_bool("PYMTA_ENABLE_SMTPUTF8", True)

# PROXY protocol v1/v2 (HAProxy in front). Mirrors the Postfix
# ENABLE_PROXY_PROTOCOL=haproxy env knob.
PYMTA_ENABLE_PROXY_PROTOCOL = _env_str(
    "ENABLE_PROXY_PROTOCOL", ""
).lower() == "haproxy" or _env_bool("PYMTA_ENABLE_PROXY_PROTOCOL", False)
PYMTA_PROXY_PROTOCOL_TIMEOUT = _env_int("PYMTA_PROXY_PROTOCOL_TIMEOUT", 5)


# ---------------------------------------------------------------------------
# STARTTLS (opportunistic). When both files are set, STARTTLS is advertised.
# ---------------------------------------------------------------------------

PYMTA_TLS_CERT_FILE = _env_str("PYMTA_TLS_CERT_FILE", "")
PYMTA_TLS_KEY_FILE = _env_str("PYMTA_TLS_KEY_FILE", "")


# ---------------------------------------------------------------------------
# Prometheus metrics HTTP endpoint
# ---------------------------------------------------------------------------

PYMTA_METRICS_HOST = _env_str("PYMTA_METRICS_HOST", "0.0.0.0")  # noqa: S104
# Set to 0 to disable the metrics HTTP server.
PYMTA_METRICS_PORT = _env_int("PYMTA_METRICS_PORT", 9100)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

PYMTA_LOG_LEVEL = _env_str("PYMTA_LOG_LEVEL", "INFO").upper()
