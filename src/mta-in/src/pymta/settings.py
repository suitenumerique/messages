"""Environment-variable-driven settings for the pymta server."""

import ipaddress
import os
import ssl

from .address import AddressError, validate_envelope_address


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean setting.

    Unset or blank takes the default. Anything else must be a known spelling:
    a typo read as its opposite could leave a security toggle off.
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
    """Read an integer setting, at least ``minimum``.

    ``minimum=0`` marks the settings where zero means "disabled", ``minimum=1``
    those where zero would break mail without an error:
    ``PYMTA_MAX_RECIPIENTS_PER_ENVELOPE=0`` would 452 every recipient,
    ``PYMTA_DATA_TIMEOUT=0`` would cut every DATA phase as it starts.
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


# Postfix-image variables read because their pymta counterpart was unset, as
# (legacy name, pymta name). Warned about at startup.
LEGACY_IN_USE: set[tuple[str, str]] = set()


def _env_fraction(name: str, default: float) -> float:
    """Read a share of something: a number above 0, up to 1."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be a number, got {raw!r}") from exc
    # Written this way round so that NaN is refused too.
    if not 0 < value <= 1:
        raise ValueError(f"Environment variable {name} must be above 0 and at most 1, got {raw!r}")
    return value


def _env_name(name: str, legacy: str) -> str:
    """Which variable to read: the prefixed one, or the Postfix image's.

    Both images can run from one env file during a switchover. The prefixed
    name wins when set; use of the old one is recorded for the startup warning.
    """
    if not os.environ.get(name, "").strip() and os.environ.get(legacy, "").strip():
        LEGACY_IN_USE.add((legacy, name))
        return legacy
    return name


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw


def _env_token(name: str, default: str, *, spaces: bool = False) -> str:
    """Read a setting that goes into an SMTP reply line.

    The banner is ``220 {hostname} {ident}``: a CR, LF or NUL would add lines
    to the greeting. The operator sets these, not the peer, but
    ``PYMTA_SMTP_HOSTNAME`` falls back to the shared ``MYHOSTNAME``, which on
    k8s can come from the downward API.

    ASCII only: the greeting comes before any extension is agreed on, and is
    written as ASCII when SMTPUTF8 is off. ``spaces`` allows them, for the
    text after the hostname.
    """
    value = _env_str(name, default)
    if not value.isascii() or not value.isprintable():
        raise ValueError(f"Environment variable {name} must be printable ASCII")
    if not spaces and " " in value:
        raise ValueError(f"Environment variable {name} must not contain spaces")
    return value


# ---------------------------------------------------------------------------
# MDA back-end
# ---------------------------------------------------------------------------

MDA_API_BASE_URL = _env_str("MDA_API_BASE_URL", "http://localhost:8000/api/v1.0/")
MDA_API_SECRET = _env_str("MDA_API_SECRET", "")
MDA_API_TIMEOUT = _env_int("MDA_API_TIMEOUT", 30, minimum=1)

# Lifetime of the HS256 token signed for each MDA call. It has to cover the
# request (at most MDA_API_TIMEOUT) plus the clock skew with the MDA: an
# ``exp`` in the MDA's past is a 401, which defers the mail until the clocks
# agree. The body_hash claim ties a captured token to its request, so the
# margin costs nothing.
MDA_API_JWT_TTL = _env_int("MDA_API_JWT_TTL", MDA_API_TIMEOUT + 90, minimum=1)

# Circuit breaker on the MDA calls: after this many consecutive failures
# (timeout, transport error, 5xx that is not the MDA's verdict on a message),
# calls are answered 451 without being made for ``MDA_BREAKER_COOLDOWN``
# seconds, so sessions do not pile up against a dead MDA. pymta only: the
# milter does not read these.
#
# A threshold of 0 switches it off. The cooldown is at least 1: at 0 the
# breaker would open and let the very next call through.
MDA_BREAKER_THRESHOLD = _env_int("MDA_BREAKER_THRESHOLD", 10, minimum=0)
MDA_BREAKER_COOLDOWN = _env_int("MDA_BREAKER_COOLDOWN", 30, minimum=1)


# ---------------------------------------------------------------------------
# SMTP listener
# ---------------------------------------------------------------------------

PYMTA_SMTP_BIND_HOST = _env_str("PYMTA_SMTP_BIND_HOST", "0.0.0.0")  # noqa: S104
PYMTA_SMTP_BIND_PORT = _env_int("PYMTA_SMTP_BIND_PORT", 25, minimum=1)

# Hostname in the banner and in ``Received``, as Postfix's ``myhostname``.
PYMTA_SMTP_HOSTNAME = _env_token(_env_name("PYMTA_SMTP_HOSTNAME", "MYHOSTNAME"), "mta-in")

# After the hostname in the banner. No software name or version, for scanners.
PYMTA_SMTP_IDENT = _env_token("PYMTA_SMTP_IDENT", "ESMTP", spaces=True)


# ---------------------------------------------------------------------------
# Message-shape limits (security-critical)
# ---------------------------------------------------------------------------

# Message size cap, also advertised as the ESMTP SIZE.
#
# The default is the MDA's own: above it, pymta would take a whole body for
# the MDA to answer 413; below it, pymta would refuse mail the MDA stores.
PYMTA_MAX_INCOMING_EMAIL_SIZE = _env_int(
    _env_name("PYMTA_MAX_INCOMING_EMAIL_SIZE", "MAX_INCOMING_EMAIL_SIZE"),
    10 * 1024 * 1024,
    minimum=1,
)

# RCPT TO per transaction, as Postfix's ``smtpd_recipient_limit=100``.
#
# controller.py allows (this x PYMTA_MAX_ENVELOPES_PER_SESSION) + 10 RCPT
# commands per session, 10010 on the defaults: raising either raises how many
# commands one connection can spend re-arming the idle timer.
PYMTA_MAX_RECIPIENTS_PER_ENVELOPE = _env_int("PYMTA_MAX_RECIPIENTS_PER_ENVELOPE", 100, minimum=1)

# Messages per TCP connection. The MAIL after the last one is answered 421,
# and that message waits for the sender's next queue run: set high enough to
# be rare.
PYMTA_MAX_ENVELOPES_PER_SESSION = _env_int("PYMTA_MAX_ENVELOPES_PER_SESSION", 100, minimum=1)

# Longest line accepted in DATA or a command, in octets, line ending excluded.
# A longer one is answered 500, a permanent rejection.
#
# RFC 5321 §4.5.3.1.6 says 998, which long unwrapped HTML and URLs exceed all
# the time. Postfix receives such mail: its ``line_length_limit`` of 2048 is
# about how the cleanup daemon wraps output, not what smtpd receives.
#
# 64 KiB still bounds the read buffer: ``wire.LineReader`` holds about twice
# this, per session, before it pauses the peer. Command lines are held to 512
# octets (548 for MAIL) once read.
PYMTA_MAX_LINE_LENGTH = _env_int("PYMTA_MAX_LINE_LENGTH", 65536, minimum=998)

# Messages that may be in memory at once.
#
# Each message is held in RAM, so this is what bounds memory: a session costs
# a few kB until it says DATA. A message peaks at about 2.2 times its size
# (see ``server._MEMORY_FACTOR``), so 40 x 10 MiB is about 880 MiB and takes a
# container of 1 GiB or more. Use 20 for 512 MiB.
#
# 0 is for dev and tests: PYMTA_MAX_SESSIONS_TOTAL is derived from this, so it
# also removes the session cap and the per-source shares.
#
# The slots are shared among the sources present, like the session slots: see
# PYMTA_MAX_SHARE_PER_SOURCE.
PYMTA_MAX_CONCURRENT_DATA = _env_int("PYMTA_MAX_CONCURRENT_DATA", 40, minimum=0)

# RFC 5321 §4.5.3.1.1 and .1.2: local-part of 64 octets, domain of 255. The
# protocol's numbers, so not env vars; ``validate_envelope_address`` takes
# them as arguments for the tests.
#
# The local-part cap is for RCPT only, where a higher one would accept what
# the MDA cannot store. MAIL FROM has none (a forwarder's SRS return path is
# often longer) and no path cap either: §4.5.3.1 asks to avoid these limits
# and Postfix enforced neither. The 548-octet MAIL command line bounds it
# (see ``SMTPConnection._line_limit``).
PYMTA_MAX_LOCAL_PART = 64
PYMTA_MAX_DOMAIN = 255


# ---------------------------------------------------------------------------
# Timeouts & connection caps
# ---------------------------------------------------------------------------

# Seconds to wait for the next command, counted from our last reply, and the
# time one command other than DATA may take. 300 is Postfix's
# ``smtpd_timeout`` and the minimum RFC 5321 §4.5.3.2.7 asks for. With the
# server close to full, the wait for a command is 10 s: see
# ``SMTPConnection._arm_idle_timer``.
PYMTA_COMMAND_TIMEOUT = _env_int("PYMTA_COMMAND_TIMEOUT", 300, minimum=1)

# Part of the DATA deadline the handler keeps back, so it can still answer 451
# before the deadline closes the connection. PYMTA_DATA_TIMEOUT must be above
# it: at or under, the deliver call would have no time and every message would
# be deferred.
PYMTA_REPLY_RESERVE_SECONDS = 10

# Deadline for the whole DATA phase (seconds): the 354, the body, the MDA
# deliver call and the reply. It replaces the command timeout while DATA runs
# (see ``SMTPConnection._data``), so a peer sending its body slowly cannot
# outlast it.
#
# It sets how slow a legitimate sender may be: 10 MiB in 300 s is about
# 35 kB/s.
PYMTA_DATA_TIMEOUT = _env_int("PYMTA_DATA_TIMEOUT", 300, minimum=PYMTA_REPLY_RESERVE_SECONDS + 1)

# Longest one TCP session may last (seconds), armed at connect and never
# re-armed. 0 disables. A message being received or delivered when it fires is
# finished first.
#
# Every other timeout is reset by activity: a peer sending one command just
# under PYMTA_COMMAND_TIMEOUT could otherwise hold a slot for as long as
# ``command_call_limit`` lets it. An attacker can reconnect, but once it is
# blocked its slots are freed.
#
# 100 recipients and a 10 MB body take a real sender under a minute.
PYMTA_SESSION_TIMEOUT = _env_int("PYMTA_SESSION_TIMEOUT", 1800, minimum=0)

# Seconds the server waits for sessions in flight after SIGTERM before it
# abandons them. Keep it under k8s ``terminationGracePeriodSeconds``, so the
# cut is ours and not SIGKILL's.
#
# Far below PYMTA_DATA_TIMEOUT and PYMTA_SESSION_TIMEOUT: a rollout cuts a
# slow DATA phase. A peer cut off before our 250 retries, which can duplicate
# a message already delivered but does not lose one.
PYMTA_SHUTDOWN_TIMEOUT = _env_int("PYMTA_SHUTDOWN_TIMEOUT", 25, minimum=0)

# Concurrent SMTP sessions, process-wide: the message limit times the sessions
# it takes to keep one message slot busy.
#
# DATA is about a third of a session at typical sizes; the rest is handshake
# and RCPT checks. Fewer than three sessions per slot leaves message slots
# idle; more admits connections that pay for their RCPT checks and are then
# refused a slot at DATA.
#
# Not read from the environment, but prefixed like the settings of this server
# (MDA_ is for the channel to the MDA, a bare name is shared with the Postfix
# image).
#
# PYMTA_MAX_CONCURRENT_DATA=0 makes this 0 and switches both refusals off in
# IPGate, which dev and tests rely on.
_SESSIONS_PER_DATA_SLOT = 3
PYMTA_MAX_SESSIONS_TOTAL = PYMTA_MAX_CONCURRENT_DATA * _SESSIONS_PER_DATA_SLOT

# Most of the session slots, and of the message slots, one source may hold, as
# a fraction. A source is an IPv4 address or an IPv6 /64.
#
# At 0.1 it takes ten sources to fill the server; on the defaults that is 12
# sessions and 4 messages in flight per source. A sender over its share is
# answered 421 at connect, or 451 at DATA, and retries.
#
# Lower it to make the server harder to fill. Raise it when a legitimate
# sender with few addresses is held back
# (``pymta_connections_total{result="rejected_per_ip"}``).
PYMTA_MAX_SHARE_PER_SOURCE = _env_fraction("PYMTA_MAX_SHARE_PER_SOURCE", 0.1)

# 4xx/5xx replies a session may collect, as Postfix's
# ``smtpd_hard_error_limit``: at this many, its next command is answered 421
# and the connection closed. Against address enumeration within one session.
PYMTA_MAX_ERRORS_PER_SESSION = _env_int("PYMTA_MAX_ERRORS_PER_SESSION", 50, minimum=1)

# Unknown recipients a session may name before it is answered 421 and closed.
# Stops enumeration with well-formed addresses sooner than the error budget
# above, which counts every 4xx/5xx.
PYMTA_MAX_RCPT_MISSES_PER_SESSION = _env_int("PYMTA_MAX_RCPT_MISSES_PER_SESSION", 10, minimum=1)


# ---------------------------------------------------------------------------
# ESMTP feature toggles
# ---------------------------------------------------------------------------

PYMTA_ENABLE_SMTPUTF8 = _env_bool("PYMTA_ENABLE_SMTPUTF8", True)


def _env_proxy_protocol(name: str, default: bool) -> bool:
    """Read the PROXY-protocol switch: a boolean, or ``haproxy`` for true.

    Postfix's ``postscreen_upstream_proxy_protocol`` takes a protocol name and
    defines only ``haproxy``, the one implemented here (v1 and v2).
    """
    if os.environ.get(name, "").strip().lower() == "haproxy":
        return True
    return _env_bool(name, default)


# PROXY protocol v1/v2 (a load balancer in front).
PYMTA_ENABLE_PROXY_PROTOCOL = _env_proxy_protocol(
    _env_name("PYMTA_ENABLE_PROXY_PROTOCOL", "ENABLE_PROXY_PROTOCOL"), False
)
PYMTA_PROXY_PROTOCOL_TIMEOUT = _env_int("PYMTA_PROXY_PROTOCOL_TIMEOUT", 5, minimum=1)


def parse_client_ip(raw: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse a client address, unwrapping the IPv4-mapped IPv6 form.

    A dual-stack listener (``PYMTA_SMTP_BIND_HOST=::``) reports an IPv4 peer as
    ``::ffff:198.51.100.7``, which ``ipaddress`` does not find in
    ``198.51.100.0/24``. A blocked /24, or a balancer's IPv4 range in
    PYMTA_TRUSTED_PROXIES, would never match, without any error.
    """
    if not raw:
        return None
    try:
        addr = ipaddress.ip_address(str(raw))
    except ValueError:
        return None
    # ipv4_mapped exists only on IPv6Address, and is None for a native v6 one.
    return getattr(addr, "ipv4_mapped", None) or addr


def canonical_ip(raw: str | None) -> str | None:
    """A client address as it is kept: IPv4 for the IPv4-mapped IPv6 form.

    Logged and sent to the MDA, which would otherwise record an IPv4 sender
    as an IPv6 one. Unchanged when it does not parse.
    """
    addr = parse_client_ip(raw)
    return raw if addr is None else str(addr)


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


# Comma-separated IPs / CIDRs allowed to send a PROXY header, matched against
# the wire peer (the TCP source: the load balancer). The source the header
# names becomes the key of the per-source limits and the address the MDA
# writes into ``Received``. A peer outside the list is closed before its
# header is read.
#
# Required with PROXY protocol. A deployment that cannot name its balancer
# sets ``0.0.0.0/0,::/0``: the SMTP port must then be reachable by the
# balancer only.
PYMTA_TRUSTED_PROXIES = _env_networks("PYMTA_TRUSTED_PROXIES")
if PYMTA_ENABLE_PROXY_PROTOCOL and not PYMTA_TRUSTED_PROXIES:
    raise ValueError(
        "PYMTA_ENABLE_PROXY_PROTOCOL is set but PYMTA_TRUSTED_PROXIES is empty: name the "
        "balancer's addresses, or 0.0.0.0/0,::/0 to trust every peer."
    )


# ---------------------------------------------------------------------------
# Client blocklist
# ---------------------------------------------------------------------------

# Comma-separated IPs / CIDRs refused at connect, matched against the client
# address: the PROXY-header source with PROXY protocol, the TCP peer
# otherwise. Unrelated to PYMTA_TRUSTED_PROXIES.
#
# For an incident where one netblock is the problem: it applies after a
# restart, without a firewall or a load balancer change.
#
# The reply is a permanent 554, not a 421: a deferred sender comes back every
# few minutes. A block that covers a legitimate sender bounces its mail, so
# keep the ranges narrow and remove them afterwards.
PYMTA_BLOCKED_NETWORKS = _env_networks("PYMTA_BLOCKED_NETWORKS")


def _env_lower_set(name: str) -> frozenset[str]:
    """Comma-separated list, lower-cased. Empty entries dropped."""
    return frozenset(
        chunk.strip().lower() for chunk in _env_str(name, "").split(",") if chunk.strip()
    )


# Sender domains refused at MAIL FROM with 554, matched case-insensitively on
# the part after the '@'. For a joe-job forging one domain, which
# PYMTA_BLOCKED_NETWORKS cannot stop.
PYMTA_BLOCKED_SENDER_DOMAINS = _env_lower_set("PYMTA_BLOCKED_SENDER_DOMAINS")

# Recipient addresses refused at RCPT TO with 550, matched case-insensitively.
# Takes one mailbox out of an attack without touching the MDA.
PYMTA_BLOCKED_RECIPIENTS = _env_lower_set("PYMTA_BLOCKED_RECIPIENTS")

# Separates a mailbox name from a tag in a recipient local-part
# (``user+tag@domain``), as Postfix's ``recipient_delimiter``. Used only so
# that PYMTA_BLOCKED_RECIPIENTS also matches the tagged forms of an address;
# which mailbox a tagged address belongs to is the MDA's decision. Empty
# disables it. Must be the delimiter the MDA uses once it resolves tags.
PYMTA_RECIPIENT_DELIMITER = os.environ.get("PYMTA_RECIPIENT_DELIMITER", "+").strip()
if len(PYMTA_RECIPIENT_DELIMITER) > 1 or PYMTA_RECIPIENT_DELIMITER in ("@", "."):
    raise ValueError(
        "Environment variable PYMTA_RECIPIENT_DELIMITER must be a single character "
        f"other than '@' and '.', or empty; got {PYMTA_RECIPIENT_DELIMITER!r}"
    )


def _env_address(name: str) -> str:
    """Read an optional envelope address, refusing one RCPT would not accept."""
    value = _env_str(name, "").strip()
    if not value:
        return ""
    try:
        return validate_envelope_address(
            value, allow_empty=False, max_local=PYMTA_MAX_LOCAL_PART, max_domain=PYMTA_MAX_DOMAIN
        )
    except AddressError as exc:
        raise ValueError(
            f"Environment variable {name} is set to {value!r}, which is not a valid "
            f"recipient address: {exc.smtp_text}"
        ) from exc


# Mailbox that receives ``RCPT TO:<postmaster>``, the bare form with no domain
# that RFC 5321 §4.5.1 requires a server to accept. The recipient is rewritten
# to this address and then checked with the MDA like any other. Empty leaves
# the bare form refused (501), since there is no domain to deliver it to.
# ``postmaster@<domain>`` is not affected: it is an ordinary address.
PYMTA_POSTMASTER_ADDRESS = _env_address("PYMTA_POSTMASTER_ADDRESS")

# Refuse a MAIL FROM whose domain is not fully qualified: a single label such
# as ``root@localhost`` (504), or an address literal such as
# ``user@[192.0.2.1]`` (501). Neither can be answered through an MX.
PYMTA_REQUIRE_FQDN_SENDER = _env_bool("PYMTA_REQUIRE_FQDN_SENDER", True)


# ---------------------------------------------------------------------------
# Crisis toggles
# ---------------------------------------------------------------------------

# Accept the dialogue but no mail: every RCPT answers 451. Senders queue and
# retry for days, so nothing is lost while the MDA is broken or an incident is
# worked on. Stopping the container does the same for a well-behaved sender,
# but leaves no log line and no metric.
PYMTA_DEFER_ALL = _env_bool("PYMTA_DEFER_ALL", False)


# No setting is re-read while the process runs: a change is a restart, which
# the SIGTERM drain makes graceful. SIGHUP re-reads the certificate files only
# (``controller.Listener.reload_tls``).
#
# Future idea: read the crisis toggles above from a mounted file on SIGHUP.
# The handler, the gate and the protocol already read these values from this
# module at each use.


# ---------------------------------------------------------------------------
# STARTTLS (opportunistic), advertised when both files are set.
#
# Both are comma-separated lists of the same length, paired by position, so a
# server can hold one certificate per key type (ECDSA and RSA): OpenSSL
# presents the one the client can verify. One entry each is the usual case.
# ---------------------------------------------------------------------------

PYMTA_TLS_CERT_FILE = _env_str("PYMTA_TLS_CERT_FILE", "")
PYMTA_TLS_KEY_FILE = _env_str("PYMTA_TLS_KEY_FILE", "")

# Postfix-style bundle, read only when neither path is set. Postfix puts the
# key and the chain in one PEM, which Python's ssl accepts as both certfile
# and keyfile.
_chain = os.environ.get("STARTTLS_CHAIN_FILES", "").strip()
if _chain and not PYMTA_TLS_CERT_FILE and not PYMTA_TLS_KEY_FILE:
    LEGACY_IN_USE.add(("STARTTLS_CHAIN_FILES", "PYMTA_TLS_CERT_FILE + PYMTA_TLS_KEY_FILE"))
    PYMTA_TLS_CERT_FILE = PYMTA_TLS_KEY_FILE = _chain


def _tls_paths(raw: str, name: str) -> tuple[str, ...]:
    """Split a comma-separated path list, dropping empty entries.

    A trailing comma is accepted, as Postfix does: the value may have been
    written for that image. A value that names no path is refused: the pair
    check below would read it as STARTTLS switched off.
    """
    if not raw:
        return ()
    paths = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not paths:
        raise ValueError(
            f"Environment variable {name} is set to {raw!r}, which names no "
            "path. Give one path per certificate, comma-separated, or leave it "
            "empty to disable STARTTLS deliberately."
        )
    return paths


# The variable the operator set, for the error above.
_tls_names = ("PYMTA_TLS_CERT_FILE", "PYMTA_TLS_KEY_FILE")
if _chain and PYMTA_TLS_CERT_FILE == PYMTA_TLS_KEY_FILE == _chain:
    _tls_names = ("STARTTLS_CHAIN_FILES", "STARTTLS_CHAIN_FILES")

_certs = _tls_paths(PYMTA_TLS_CERT_FILE, _tls_names[0])
_keys = _tls_paths(PYMTA_TLS_KEY_FILE, _tls_names[1])

# Both or neither: one without the other is a mistake, and must not end as
# STARTTLS switched off.
if bool(_certs) != bool(_keys):
    raise ValueError(
        "PYMTA_TLS_CERT_FILE and PYMTA_TLS_KEY_FILE must be set together; "
        f"only {'PYMTA_TLS_CERT_FILE' if _certs else 'PYMTA_TLS_KEY_FILE'} "
        "is set, which would leave STARTTLS off without saying so. Leave both "
        "empty to disable STARTTLS deliberately."
    )

# Paired by position: with different lengths a certificate would be loaded
# with another's key, which fails in OpenSSL with a message that names neither
# variable.
if len(_certs) != len(_keys):
    raise ValueError(
        f"PYMTA_TLS_CERT_FILE lists {len(_certs)} path(s) and "
        f"PYMTA_TLS_KEY_FILE lists {len(_keys)}. They are paired in order, so "
        "each certificate needs its key at the same position."
    )

# What ``load_tls_context`` consumes: one (cert, key) pair per certificate.
PYMTA_TLS_CERT_PAIRS: tuple[tuple[str, str], ...] = tuple(zip(_certs, _keys, strict=True))

# Refuse mail on a session that has not done STARTTLS: every command but
# EHLO, STARTTLS, NOOP and QUIT is answered 530 until it has.
#
# Off by default, and for a listener whose senders are known (a private relay,
# a partner link). On a public MX it loses the mail of every sender that
# cannot do TLS, or that fails the handshake and retries in clear. To get
# encryption from those that can without losing the others, publish MTA-STS
# or DANE for the domain: the sender then enforces it.
PYMTA_REQUIRE_TLS = _env_bool("PYMTA_REQUIRE_TLS", False)
if PYMTA_REQUIRE_TLS and not PYMTA_TLS_CERT_PAIRS:
    raise ValueError(
        "PYMTA_REQUIRE_TLS is set but no certificate is configured "
        "(PYMTA_TLS_CERT_FILE / PYMTA_TLS_KEY_FILE): every message would be refused."
    )

# Oldest TLS version accepted on STARTTLS: 1.0, 1.1, 1.2 or 1.3.
#
# 1.0 by default, as the large mail providers and the Postfix image this
# replaces do. A sender that only speaks 1.0 or 1.1 and is refused fails the
# handshake, then either retries in clear or gives up, so on an inbound MX an
# old protocol is better than none. The cost: a minimum below 1.2 drops
# OpenSSL to security level 0 for every handshake on this listener, because
# the SHA-1 signatures those versions use are refused above it. Set 1.2 to
# refuse them.
_TLS_VERSIONS = ("1.0", "1.1", "1.2", "1.3")
PYMTA_TLS_MIN_VERSION = _env_str("PYMTA_TLS_MIN_VERSION", "1.0").strip()
if PYMTA_TLS_MIN_VERSION not in _TLS_VERSIONS:
    raise ValueError(
        f"Environment variable PYMTA_TLS_MIN_VERSION is set to {PYMTA_TLS_MIN_VERSION!r}. "
        f"Use one of {', '.join(_TLS_VERSIONS)}."
    )

# OpenSSL cipher list for TLS 1.2 and below (TLS 1.3 suites are not affected).
# Empty keeps Python's default list, which accepts what most senders offer,
# CBC and SHA-1 suites included. A narrower list drops the weak suites and
# turns away the senders that can use nothing else, who then retry in clear or
# give up.
PYMTA_TLS_CIPHERS = _env_str("PYMTA_TLS_CIPHERS", "").strip()
if PYMTA_TLS_CIPHERS:
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).set_ciphers(PYMTA_TLS_CIPHERS)
    except ssl.SSLError as exc:
        raise ValueError(
            f"Environment variable PYMTA_TLS_CIPHERS is set to {PYMTA_TLS_CIPHERS!r}, "
            "which OpenSSL does not accept as a cipher list or which matches no suite."
        ) from exc


# ---------------------------------------------------------------------------
# Prometheus metrics HTTP endpoint
# ---------------------------------------------------------------------------

# All interfaces by default: a k8s Prometheus scraping the pod IP, or a
# compose port mapping, cannot reach a loopback listener. The exposition has
# no addresses and no message data, but shows volumes, rejection reasons and
# breaker state. Keep it off the interface that serves port 25 with a
# NetworkPolicy or a firewall rule; failing that, set the host to 127.0.0.1 or
# the port to 0.
PYMTA_METRICS_BIND_HOST = _env_str("PYMTA_METRICS_BIND_HOST", "0.0.0.0")  # noqa: S104
# Set to 0 to disable the metrics HTTP server.
PYMTA_METRICS_BIND_PORT = _env_int("PYMTA_METRICS_BIND_PORT", 9100, minimum=0)

# Bearer token required to scrape /metrics. Empty means no authentication,
# which ``metrics.start_metrics_server`` warns about at startup. Same
# arrangement as the backend's PROMETHEUS_API_KEY.
PYMTA_METRICS_API_KEY = _env_str("PYMTA_METRICS_API_KEY", "")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

# Let third-party loggers through at PYMTA_LOG_LEVEL (see
# logfmt.OwnRecordsOnly). For debugging only: httpx and httpcore then trace
# every MDA request, headers included.
PYMTA_LOG_VERBOSE_LIBRARIES = _env_bool("PYMTA_LOG_VERBOSE_LIBRARIES", False)

# One ``session_closed`` line per admitted connection, at INFO: who connected,
# what they said, how it ended. The only record of a sender that never got to
# a message outcome (TLS refused, a command refused, a timeout, a hang-up).
# Under a connection flood, set it to false and the line drops to DEBUG.
PYMTA_LOG_SESSIONS = _env_bool("PYMTA_LOG_SESSIONS", True)

# Checked here, so the error names the variable.
_LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")
PYMTA_LOG_LEVEL = _env_str("PYMTA_LOG_LEVEL", "INFO").upper()
if PYMTA_LOG_LEVEL not in _LOG_LEVELS:
    raise ValueError(
        f"Environment variable PYMTA_LOG_LEVEL is set to {PYMTA_LOG_LEVEL!r}. "
        f"Use one of {', '.join(_LOG_LEVELS)}."
    )
