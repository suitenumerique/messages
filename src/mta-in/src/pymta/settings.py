"""Environment-variable-driven settings for the pymta server."""

import ipaddress
import os


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean setting, refusing spellings the code cannot honour.

    An unset or blank variable takes the default. Anything else must be a
    recognised spelling: silently reading a typo as its opposite is how a
    security toggle ends up off in production.
    """
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(
        f"Environment variable {name} is set to {raw!r}, which is not a recognised "
        "boolean. Use one of 1/true/yes/on or 0/false/no/off."
    )


def _env_int(name: str, default: int, *, minimum: int) -> int:
    """Read an integer setting, refusing values the code cannot honour.

    ``minimum`` is part of each setting's contract and is written out at every
    call site: ``minimum=0`` marks the settings where zero means "disabled",
    ``minimum=1`` the ones where zero is nonsense. Without the check those two
    groups look identical from the env, and the nonsense values fail in ways
    that are silent and wrong rather than loud. ``PYMTA_MAX_RECIPIENTS_PER_ENVELOPE=0``
    would 452 every recipient, ``PYMTA_MAX_INCOMING_EMAIL_SIZE=0`` would disable
    aiosmtpd's size cap while the handler rejected every message, and
    ``PYMTA_DATA_TIMEOUT=0`` would quietly inherit the command timeout because
    aiosmtpd reads a falsy duration as "unset".
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"Environment variable {name} must be >= {minimum}, got {value}")
    return value


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw


def _env_token(name: str, default: str) -> str:
    """Read a setting that gets interpolated into an SMTP reply line.

    The banner is ``220 {hostname} {ident}``, so a CR/LF/NUL here would append
    extra lines to our greeting. These values come from the operator, not the
    peer, so this is not a live vector; ``PYMTA_SMTP_HOSTNAME`` does fall back to
    the shared ``MYHOSTNAME``, which on k8s can come from the downward API.
    """
    value = _env_str(name, default)
    if any(c in value for c in ("\r", "\n", "\x00", "\t")):
        raise ValueError(f"Environment variable {name} must not contain control characters")
    return value


# ---------------------------------------------------------------------------
# MDA back-end
# ---------------------------------------------------------------------------

MDA_API_BASE_URL = _env_str("MDA_API_BASE_URL", "http://localhost:8000/api/v1.0/")
MDA_API_SECRET = _env_str("MDA_API_SECRET", "")
MDA_API_TIMEOUT = _env_int("MDA_API_TIMEOUT", 30, minimum=1)

# Lifetime of the HS256 token signed for each MDA call. It must comfortably
# cover the whole request (bounded by MDA_API_TIMEOUT) *plus* whatever clock
# skew exists between this process and the MDA. An `exp` in the MDA's past is
# a 401, which under the classification below defers the mail instead of
# bouncing it, but still stalls delivery until the clocks agree. The body_hash
# claim keeps a captured token usable only for its exact request, so the extra
# margin costs nothing.
MDA_API_JWT_TTL = _env_int("MDA_API_JWT_TTL", MDA_API_TIMEOUT + 90, minimum=1)

# Circuit-breaker on the MDA calls. When this many consecutive calls fail
# (timeout / 5xx / transport error), pymta short-circuits subsequent ones for
# ``MDA_BREAKER_COOLDOWN`` seconds and replies 451 directly, which stops SMTP
# sessions stacking up against a dead MDA. Only pymta implements it; the milter
# ignores these, as it ignores anything it does not read.
#
# The threshold is the off switch (0 = never trip). The cooldown takes minimum=1
# because a zero there disables nothing: the breaker would trip and reopen to the
# very next call, which is the breaker neutered rather than turned off.
MDA_BREAKER_THRESHOLD = _env_int("MDA_BREAKER_THRESHOLD", 10, minimum=0)
MDA_BREAKER_COOLDOWN = _env_int("MDA_BREAKER_COOLDOWN", 30, minimum=1)


# ---------------------------------------------------------------------------
# SMTP listener
# ---------------------------------------------------------------------------

PYMTA_SMTP_BIND_HOST = _env_str("PYMTA_SMTP_BIND_HOST", "0.0.0.0")  # noqa: S104
PYMTA_SMTP_BIND_PORT = _env_int("PYMTA_SMTP_BIND_PORT", 25, minimum=1)

# Banner / Received-header hostname. Matches Postfix's `myhostname`.
PYMTA_SMTP_HOSTNAME = _env_token("PYMTA_SMTP_HOSTNAME", _env_str("MYHOSTNAME", "mta-in"))

# ESMTP banner ident (after the hostname). Kept short and version-less so we
# don't broadcast "aiosmtpd X.Y.Z" to internet scanners.
PYMTA_SMTP_IDENT = _env_token("PYMTA_SMTP_IDENT", "ESMTP")


# ---------------------------------------------------------------------------
# Message-shape limits (security-critical)
# ---------------------------------------------------------------------------

# Total RFC822 message size cap, and the value advertised as the ESMTP SIZE.
#
# The unprefixed ``MAX_INCOMING_EMAIL_SIZE`` is the Postfix image's name for the
# same limit (entrypoint.sh writes it into `message_size_limit`) and is read here
# only as a migration fallback, so an env file written for that image keeps
# working. Reading it through ``_env_int`` as well means a malformed value fails
# at startup whichever name carries it.
#
# The final fallback matches the MDA's own default (10 MiB): a cap above it
# would have pymta accept the whole body only for the MDA to answer 413, and a
# cap below it would refuse mail the MDA would have stored.
PYMTA_MAX_INCOMING_EMAIL_SIZE = _env_int(
    "PYMTA_MAX_INCOMING_EMAIL_SIZE",
    _env_int("MAX_INCOMING_EMAIL_SIZE", 10 * 1024 * 1024, minimum=1),
    minimum=1,
)

# RCPT TO per SMTP transaction. Mirrors Postfix `smtpd_recipient_limit=100`.
#
# Note the multiplication downstream: controller.py derives the per-verb
# ``command_call_limit`` for RCPT as (this x MAX_ENVELOPES_PER_CONNECTION) + 10,
# which on the defaults is 1010 commands before aiosmtpd force-closes. Raising
# either of these raises that ceiling as their product, and it is the ceiling
# on how many commands one connection can spend re-arming the idle timer.
PYMTA_MAX_RECIPIENTS_PER_ENVELOPE = _env_int("PYMTA_MAX_RECIPIENTS_PER_ENVELOPE", 100, minimum=1)

# Envelopes per TCP connection (one envelope = MAIL FROM..DATA cycle).
PYMTA_MAX_ENVELOPES_PER_SESSION = _env_int("PYMTA_MAX_ENVELOPES_PER_SESSION", 10, minimum=1)

# RFC 5321 §4.5.3.1.1/.1.2: local-part ≤ 64 octets, domain ≤ 255 octets.
# Constants, not env vars: these are the protocol's numbers, not a deployment
# choice, and raising them would only widen the gap between what pymta accepts
# at RCPT and what the MDA can store. ``validate_envelope_address`` still takes
# them as arguments so the tests can probe the boundaries directly.
MAX_LOCAL_PART = 64
MAX_DOMAIN = 255


# ---------------------------------------------------------------------------
# Timeouts & connection caps
# ---------------------------------------------------------------------------

# Per-command idle timeout (seconds). Postfix default is 300 s; we tighten.
#
# aiosmtpd arms this deadline once per accepted command and only re-arms it
# when the *next* complete command line arrives. It is not re-armed while a
# command handler runs. So this is the ceiling on "peer connected / last
# command finished, nothing since".
PYMTA_COMMAND_TIMEOUT = _env_int("PYMTA_COMMAND_TIMEOUT", 120, minimum=1)

# Hard deadline for the DATA phase (seconds): 354 reply → last body byte →
# MDA deliver call → SMTP reply, as one budget. ``HardenedSMTP.smtp_DATA``
# swaps the command deadline for this one while DATA runs, so a peer that
# dribbles the body cannot outlive it. Defends against slowloris on the body.
# Nothing in a DATA phase survives past it: the transport is armed at exactly
# this value, and the handler reserves a slice of it (see
# ``handler._REPLY_RESERVE_SECONDS``) so it can still answer 451 rather than
# vanishing mid-transaction.
#
# Sizing: this is a *floor* on how slow a legitimate sender may be. At the
# 10 MiB default PYMTA_MAX_INCOMING_EMAIL_SIZE, 300 s means ~35 kB/s sustained.

# The slice of that budget the handler holds back, read as
# ``handler._REPLY_RESERVE_SECONDS``. No PYMTA_ prefix: that marks the env-backed
# settings, and this one is not configurable. It is a property of how the two
# deadlines nest, not something an operator sizes. It bounds the DATA timeout
# from below, because a budget at or under the reserve leaves the deliver call
# nothing at all and would defer every message.
REPLY_RESERVE_SECONDS = 10

PYMTA_DATA_TIMEOUT = _env_int("PYMTA_DATA_TIMEOUT", 300, minimum=REPLY_RESERVE_SECONDS + 1)

# Wall-clock ceiling on one TCP session (seconds), armed at connect and never
# re-armed. 0 disables.
#
# This is the only bound a peer cannot push back by staying busy: every other
# timeout is reset by activity, so a peer issuing one command just under
# PYMTA_COMMAND_TIMEOUT can hold a slot out of PYMTA_MAX_SESSIONS_TOTAL for as
# long as ``command_call_limit`` lets it keep issuing commands.
#
# It does not stop a distributed attacker, who can reconnect. It caps how long
# one connection can hold a slot, so blocking an abuser frees the slots instead
# of leaving them held until the process restarts.
#
# Sizing: 100 recipients plus a 10 MB body is under a minute for a real sender.
PYMTA_SESSION_TIMEOUT = _env_int("PYMTA_SESSION_TIMEOUT", 1800, minimum=0)

# Maximum wall-clock seconds the server waits for in-flight sessions to drain
# after SIGTERM, before abandoning them. Sized to sit under k8s
# `terminationGracePeriodSeconds` so we choose the cut-off rather than having
# SIGKILL choose it.
#
# It sits far below PYMTA_DATA_TIMEOUT and PYMTA_SESSION_TIMEOUT, so a
# rollout does cut sessions mid-transaction rather than waiting out a slow DATA
# phase. The failure is one-directional: a peer cut off before our 250 retries,
# which risks a duplicate on an already-delivered message, not a loss. Do not
# raise this above `terminationGracePeriodSeconds`.
PYMTA_SHUTDOWN_TIMEOUT = _env_int("PYMTA_SHUTDOWN_TIMEOUT", 25, minimum=0)

# Per-IP concurrent SMTP sessions. 0 disables the cap.
PYMTA_MAX_SESSIONS_PER_IP = _env_int("PYMTA_MAX_SESSIONS_PER_IP", 100, minimum=0)

# Process-wide concurrent SMTP sessions. 0 disables.
PYMTA_MAX_SESSIONS_TOTAL = _env_int("PYMTA_MAX_SESSIONS_TOTAL", 1000, minimum=0)

# Per-IP new-session rate, measured in a fixed 60s window. Defends against a
# peer that churns through fast open/close cycles (which never exceed the
# concurrent cap but still cost CPU/TLS handshakes/MDA RCPT checks). 0 disables.
PYMTA_MAX_SESSIONS_PER_IP_PER_MINUTE = _env_int(
    "PYMTA_MAX_SESSIONS_PER_IP_PER_MINUTE", 600, minimum=0
)

# Per-session soft-error budget. Mirrors Postfix `smtpd_hard_error_limit`:
# once a session accumulates this many 4xx/5xx replies (typically over-limit
# or unknown-recipient RCPTs), the next misbehaviour gets a 421 and the
# connection closes. Defends against bulk address enumeration that lives in
# one TCP session.
PYMTA_MAX_ERRORS_PER_SESSION = _env_int("PYMTA_MAX_ERRORS_PER_SESSION", 50, minimum=1)

# Per-session cap on unknown-mailbox lookups specifically. The hard-error
# budget above covers the *aggregate* of all 4xx/5xx replies; this one
# isolates enumeration: an attacker submitting valid-syntax addresses to
# probe which exist gets cut off after this many ``no such recipient``
# replies, even if the soft-error counter is still below its limit.
PYMTA_MAX_RCPT_MISSES_PER_SESSION = _env_int("PYMTA_MAX_RCPT_MISSES_PER_SESSION", 10, minimum=1)


# ---------------------------------------------------------------------------
# ESMTP feature toggles
# ---------------------------------------------------------------------------

PYMTA_ENABLE_SMTPUTF8 = _env_bool("PYMTA_ENABLE_SMTPUTF8", True)


def _env_proxy_protocol(name: str, default: bool) -> bool:
    """Read a PROXY-protocol switch. ``haproxy`` is an alias for true.

    Postfix's ``postscreen_upstream_proxy_protocol`` takes a protocol name and
    defines only ``haproxy``, the one implemented here (v1 and v2), so naming it
    and switching the feature on are the same statement. Everything else is a
    strict boolean.
    """
    if os.environ.get(name, "").strip().lower() == "haproxy":
        return True
    return _env_bool(name, default)


# PROXY protocol v1/v2 (a load balancer in front). Use the prefixed name;
# ENABLE_PROXY_PROTOCOL is the Postfix image's name for the same switch and is
# read as a migration fallback. Both are parsed the same way, and entrypoint.sh
# refuses the values this rejects, so the two images cannot end up disagreeing.
PYMTA_ENABLE_PROXY_PROTOCOL = _env_proxy_protocol(
    "PYMTA_ENABLE_PROXY_PROTOCOL", _env_proxy_protocol("ENABLE_PROXY_PROTOCOL", False)
)
PYMTA_PROXY_PROTOCOL_TIMEOUT = _env_int("PYMTA_PROXY_PROTOCOL_TIMEOUT", 5, minimum=1)


# Comma-separated IPs / CIDRs allowed to send a PROXY header, matched against
# the *wire* peer (the TCP source, i.e. the load balancer). Anything the header
# claims is only as trustworthy as the peer that sent it: the claimed source IP
# becomes the key for every per-IP cap and the ``client_address`` the MDA bakes
# into the Received header. A peer that reaches port 25 directly could
# otherwise scatter a forged source across the address space (defeating
# PYMTA_MAX_SESSIONS_PER_IP / _PER_MINUTE) and attribute its mail to any IP it
# likes.
#
# Strongly recommended whenever PROXY protocol is enabled, because enabling it
# *is* the claim that a balancer sits in front, so the balancer's address is
# usually a known fact. Left empty there is nothing to match on and every peer's
# header is trusted; ``server.py`` warns loudly at startup rather than refusing
# to run, since some deployments only learn the balancer's addresses later.
def _env_networks(name: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks = []
    for chunk in _env_str(name, "").split(","):
        entry = chunk.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError as exc:
            raise ValueError(
                f"Environment variable {name} entry {entry!r} is not an IP address or CIDR"
            ) from exc
    return networks


PYMTA_TRUSTED_PROXIES = _env_networks("PYMTA_TRUSTED_PROXIES")


# ---------------------------------------------------------------------------
# STARTTLS (opportunistic). When both files are set, STARTTLS is advertised.
#
# Two ways to configure STARTTLS:
#   * pymta-native: ``PYMTA_TLS_CERT_FILE`` + ``PYMTA_TLS_KEY_FILE`` (two paths).
#   * Postfix-style: ``STARTTLS_CHAIN_FILES``, a comma-separated list of PEM
#     bundle files (each bundle contains a private key followed by the cert
#     chain). pymta reads the first bundle in the list and loads it via
#     ``SSLContext.load_cert_chain(certfile=path, keyfile=path)``: Python's
#     ssl module accepts a single combined PEM that way. Postfix-compatible.
# ---------------------------------------------------------------------------

PYMTA_TLS_CERT_FILE = _env_str("PYMTA_TLS_CERT_FILE", "")
PYMTA_TLS_KEY_FILE = _env_str("PYMTA_TLS_KEY_FILE", "")

# Postfix-style fallback. Only the first path in the comma-separated list is
# used (Postfix supports multiple for RSA+ECDSA dual-cert; pymta picks the
# first chain and lets the operator add SNI later if needed).
_chain_files = _env_str("STARTTLS_CHAIN_FILES", "")
if _chain_files and not PYMTA_TLS_CERT_FILE and not PYMTA_TLS_KEY_FILE:
    _first_chain = _chain_files.split(",", 1)[0].strip()
    PYMTA_TLS_CERT_FILE = _first_chain
    PYMTA_TLS_KEY_FILE = _first_chain

# Both or neither. ``load_tls_context`` returns None unless it has a pair, so a
# half-configured STARTTLS would not fail — it would serve plaintext and simply
# not advertise STARTTLS, which is the quietest possible way to lose transport
# encryption. Refuse it here instead.
if bool(PYMTA_TLS_CERT_FILE) != bool(PYMTA_TLS_KEY_FILE):
    raise ValueError(
        "PYMTA_TLS_CERT_FILE and PYMTA_TLS_KEY_FILE must be set together; "
        f"only {'PYMTA_TLS_CERT_FILE' if PYMTA_TLS_CERT_FILE else 'PYMTA_TLS_KEY_FILE'} "
        "is set, which would leave STARTTLS off without saying so. Leave both "
        "empty to disable STARTTLS deliberately."
    )


# ---------------------------------------------------------------------------
# Prometheus metrics HTTP endpoint
# ---------------------------------------------------------------------------

# Binds all interfaces by default because the usual scrape paths (a k8s
# Prometheus hitting the pod IP, a compose port mapping) cannot reach a
# loopback-only listener. The exposition is low-cardinality (no
# addresses, no client IPs), so the exposure is operational recon (volumes,
# rejection reasons, breaker state) rather than message data. It must still be
# fenced off from the interface that serves port 25 with a NetworkPolicy or
# firewall rule; set the host to 127.0.0.1, or the port to 0, where it isn't.
PYMTA_METRICS_BIND_HOST = _env_str("PYMTA_METRICS_BIND_HOST", "0.0.0.0")  # noqa: S104
# Set to 0 to disable the metrics HTTP server.
PYMTA_METRICS_BIND_PORT = _env_int("PYMTA_METRICS_BIND_PORT", 9100, minimum=0)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

# Checked against the level names rather than passed to a ``getattr`` default:
# an unrecognised level would otherwise silently resolve to INFO, which is the
# same class of quiet misconfiguration the boolean and integer readers refuse.
_LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")
PYMTA_LOG_LEVEL = _env_str("PYMTA_LOG_LEVEL", "INFO").upper()
if PYMTA_LOG_LEVEL not in _LOG_LEVELS:
    raise ValueError(
        f"Environment variable PYMTA_LOG_LEVEL is set to {PYMTA_LOG_LEVEL!r}. "
        f"Use one of {', '.join(_LOG_LEVELS)}."
    )
