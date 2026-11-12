"""Environment-variable-driven settings for the pymta server."""

import ipaddress
import os

from .address import AddressError, validate_envelope_address


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
    would 452 every recipient, ``PYMTA_MAX_INCOMING_EMAIL_SIZE=0`` would lift
    the protocol's size cap while the handler rejected every message, and
    ``PYMTA_DATA_TIMEOUT=0`` would cut every DATA phase the moment it starts.
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


# Postfix-image variables that were read because their pymta counterpart was
# unset. Reported once at startup so a migration finishes rather than stalling
# on defaults nobody restated.
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

    Both images can run from one env file during a switchover, so the old names
    keep working. The prefixed name always wins when set; the old one is only
    consulted in its absence, and using it is recorded so startup can say so.
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
PYMTA_SMTP_HOSTNAME = _env_token(_env_name("PYMTA_SMTP_HOSTNAME", "MYHOSTNAME"), "mta-in")

# ESMTP banner ident (after the hostname). Kept short and version-less so we
# don't tell internet scanners what software answers.
PYMTA_SMTP_IDENT = _env_token("PYMTA_SMTP_IDENT", "ESMTP")


# ---------------------------------------------------------------------------
# Message-shape limits (security-critical)
# ---------------------------------------------------------------------------

# Total RFC822 message size cap, and the value advertised as the ESMTP SIZE.
#
# The default matches the MDA's own: a cap above it would have pymta accept the
# whole body only for the MDA to answer 413, and a cap below it would refuse
# mail the MDA would have stored.
PYMTA_MAX_INCOMING_EMAIL_SIZE = _env_int(
    _env_name("PYMTA_MAX_INCOMING_EMAIL_SIZE", "MAX_INCOMING_EMAIL_SIZE"),
    10 * 1024 * 1024,
    minimum=1,
)

# RCPT TO per SMTP transaction. Mirrors Postfix `smtpd_recipient_limit=100`.
#
# Note the multiplication downstream: controller.py derives the per-verb
# ``command_call_limit`` for RCPT as (this x PYMTA_MAX_ENVELOPES_PER_SESSION) + 10,
# which on the defaults is 10010 commands before the session is closed. Raising
# either of these raises that ceiling as their product, and it is the ceiling
# on how many commands one connection can spend re-arming the idle timer.
PYMTA_MAX_RECIPIENTS_PER_ENVELOPE = _env_int("PYMTA_MAX_RECIPIENTS_PER_ENVELOPE", 100, minimum=1)

# Envelopes per TCP connection (one envelope = MAIL FROM..DATA cycle). The
# next MAIL is answered 421, so a sender that reuses its connections has that
# message wait for its next queue run: high enough to be rare.
PYMTA_MAX_ENVELOPES_PER_SESSION = _env_int("PYMTA_MAX_ENVELOPES_PER_SESSION", 100, minimum=1)

# Longest single line accepted in DATA or a command, in octets, line ending
# excluded.
#
# RFC 5321 §4.5.3.1.6 says 998 (1000 with the CRLF), and a longer line is
# answered ``500 Line too long``, a permanent rejection. Postfix accepts the same message: its
# ``line_length_limit`` is 2048 and applies to how the cleanup daemon wraps
# output, not to what smtpd will receive. Long unwrapped HTML and URLs break
# 998 constantly, so the strict value bounces mail that lands today, and 5xx
# means the sender gives up rather than retrying.
#
# 64 KiB accepts effectively all real mail while still bounding the per-session
# read buffer (``wire.LineReader`` holds about twice this before it pauses the
# peer, so the ceiling is that times PYMTA_MAX_SESSIONS_TOTAL). It does not
# weaken command validation: a command line is held to 512 octets (548 for
# MAIL) once it has been read.
PYMTA_MAX_LINE_LENGTH = _env_int("PYMTA_MAX_LINE_LENGTH", 65536, minimum=998)

# Messages that may be in memory at once.
#
# 0 does not disable only this bound: PYMTA_MAX_SESSIONS_TOTAL is derived from
# it (see below) and IPGate reads a zero cap as "no refusal", so a zero here
# also removes the global session cap and the per-source share. To let more
# mail through, raise the number; 0 is for dev and test.
#
# Each message is held in RAM, so this is what stands between a burst of
# large mail and the OOM killer. The session caps do not: a connection costs a
# few kB until it says DATA.
#
# Size it against the memory the container has. A message peaks at roughly 2.2x
# its size, because joining the received blocks needs a second copy for a
# moment and freed arenas are not returned promptly, so 40 x 10 MiB is about
# 880 MiB and wants a container of 1 GiB or more. Drop it to 20 for 512 MiB.
#
# The gate shares these slots among the sources present, as it does the
# session slots: see PYMTA_MAX_SHARE_PER_SOURCE.
PYMTA_MAX_CONCURRENT_DATA = _env_int("PYMTA_MAX_CONCURRENT_DATA", 40, minimum=0)

# RFC 5321 §4.5.3.1.1/.1.2: local-part ≤ 64 octets, domain ≤ 255 octets.
# Constants, not env vars: these are the protocol's numbers, not a deployment
# choice. ``validate_envelope_address`` still takes them as arguments so the
# tests can probe the boundaries directly.
#
# The local-part cap applies to RCPT only: raising it there would only widen
# the gap between what pymta accepts and what the MDA can store. MAIL FROM
# gets no local-part cap, since a forwarder's SRS-wrapped return path exceeds
# 64 octets as a matter of course, and no path cap either (§4.5.3.1 asks to
# avoid these limits; Postfix enforced neither). The 548-octet MAIL command
# line is what bounds it (see ``SMTPConnection._line_limit``).
PYMTA_MAX_LOCAL_PART = 64
PYMTA_MAX_DOMAIN = 255


# ---------------------------------------------------------------------------
# Timeouts & connection caps
# ---------------------------------------------------------------------------

# Per-command idle timeout (seconds). 300 is Postfix's ``smtpd_timeout`` and
# the minimum RFC 5321 §4.5.3.2.7 asks a server to wait for the next command.
#
# Armed when a command is dispatched and again when its reply has gone out,
# not while it runs. So this is the ceiling on "peer connected / last command
# finished, nothing since", and on how long one command other than DATA may
# take.
PYMTA_COMMAND_TIMEOUT = _env_int("PYMTA_COMMAND_TIMEOUT", 300, minimum=1)

# The slice of the DATA deadline below that the handler holds back, so it can
# still answer 451 rather than vanishing mid-transaction. Not configurable: it
# is a property of how the two deadlines nest. It bounds the DATA timeout from
# below, because a budget at or under the reserve leaves the deliver call
# nothing at all and would defer every message.
PYMTA_REPLY_RESERVE_SECONDS = 10

# Hard deadline for the DATA phase (seconds): 354 reply → last body byte →
# MDA deliver call → SMTP reply, as one budget. ``SMTPConnection._data``
# swaps the command deadline for this one while DATA runs, so a peer that
# dribbles the body cannot outlive it.
#
# Sizing: this is a *floor* on how slow a legitimate sender may be. At the
# 10 MiB default PYMTA_MAX_INCOMING_EMAIL_SIZE, 300 s means ~35 kB/s sustained.
PYMTA_DATA_TIMEOUT = _env_int("PYMTA_DATA_TIMEOUT", 300, minimum=PYMTA_REPLY_RESERVE_SECONDS + 1)

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

# Process-wide concurrent SMTP sessions. Derived, not configured: it is the
# message limit times the number of sessions it takes to keep one message slot
# busy.
#
# A session is only *in* DATA for part of its life; the rest is handshake and
# RCPT checks against the MDA. At typical sizes DATA is about a third of a
# session, so roughly three sessions are needed per slot to stop the slots
# idling. Fewer wastes the memory that was set aside for messages; more only
# admits connections that must queue for a slot, and each one pays for its RCPT
# checks against the MDA before finding that out.
#
# Derived rather than read from the environment, but prefixed all the same:
# PYMTA_ marks what belongs to this server, MDA_ what belongs to the channel to
# the MDA, and a bare name what is shared with the Postfix image. Whether a
# value comes from the environment is a separate question from whose it is.
#
# It inherits the zero: PYMTA_MAX_CONCURRENT_DATA=0 makes this 0, disabling
# both refusals in IPGate. Documented rather than floored, so dev and test keep
# the single-source behaviour.
_SESSIONS_PER_DATA_SLOT = 3
PYMTA_MAX_SESSIONS_TOTAL = PYMTA_MAX_CONCURRENT_DATA * _SESSIONS_PER_DATA_SLOT

# Most of the session slots, and of the message slots, that one source may
# hold, as a fraction. A source is an IPv4 address or an IPv6 /64.
#
# It decides how many sources it takes to fill the server: ten at 0.1. On the
# defaults that is 12 sessions and 4 messages in flight per source. A sender
# over its share is answered 421 at connect, or 451 at DATA, and retries.
#
# Lower it to make the server harder to fill. Raise it if a legitimate sender
# that comes from few addresses is being held back
# (``pymta_connections_total{result="rejected_per_ip"}``).
PYMTA_MAX_SHARE_PER_SOURCE = _env_fraction("PYMTA_MAX_SHARE_PER_SOURCE", 0.1)

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


# PROXY protocol v1/v2 (a load balancer in front).
PYMTA_ENABLE_PROXY_PROTOCOL = _env_proxy_protocol(
    _env_name("PYMTA_ENABLE_PROXY_PROTOCOL", "ENABLE_PROXY_PROTOCOL"), False
)
PYMTA_PROXY_PROTOCOL_TIMEOUT = _env_int("PYMTA_PROXY_PROTOCOL_TIMEOUT", 5, minimum=1)


def parse_client_ip(raw: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse a client address, unwrapping the IPv4-mapped IPv6 form.

    A dual-stack listener (``PYMTA_SMTP_BIND_HOST=::``) reports an IPv4 peer as
    ``::ffff:198.51.100.7``, and that does *not* match an IPv4 CIDR:
    ``ip_address("::ffff:198.51.100.7") in ip_network("198.51.100.0/24")`` is
    False. Without this, an operator who blocks a /24 during an incident, or
    names their balancer's IPv4 range in PYMTA_TRUSTED_PROXIES, gets a rule that
    silently never matches. Both failures are quiet and both are security
    relevant, so the unwrapping happens once, here, for every consumer.
    """
    if not raw:
        return None
    try:
        addr = ipaddress.ip_address(str(raw))
    except ValueError:
        return None
    # ipv4_mapped exists only on IPv6Address, and is None for a native v6 one.
    return getattr(addr, "ipv4_mapped", None) or addr


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
# the *wire* peer (the TCP source, i.e. the load balancer). The source the
# header names becomes the key of every per-source limit and the address the
# MDA writes into ``Received``, so a peer that could send one directly would
# choose both.
#
# Required with PROXY protocol. A deployment that cannot name its balancer
# says so with ``0.0.0.0/0,::/0``, and the network isolation is then the whole
# trust boundary. Either way the SMTP port must be reachable by the balancer
# only: the header is read before the peer is checked or counted.
PYMTA_TRUSTED_PROXIES = _env_networks("PYMTA_TRUSTED_PROXIES")
if PYMTA_ENABLE_PROXY_PROTOCOL and not PYMTA_TRUSTED_PROXIES:
    raise ValueError(
        "PYMTA_ENABLE_PROXY_PROTOCOL is set but PYMTA_TRUSTED_PROXIES is empty: name the "
        "balancer's addresses, or 0.0.0.0/0,::/0 to trust every peer."
    )


# ---------------------------------------------------------------------------
# Client blocklist
# ---------------------------------------------------------------------------

# Comma-separated IPs / CIDRs refused at connect time, matched against the
# *client* address: the PROXY-header source when PROXY protocol is on, the TCP
# peer otherwise. Unrelated to PYMTA_TRUSTED_PROXIES, which decides who may
# speak PROXY protocol at all.
#
# This exists to be changed under pressure. Everything else here is a ceiling
# you size once; this is the lever you pull at 3am when one netblock is the
# problem, and it takes effect on the next connection after a restart without
# touching a firewall or a load balancer you may not control.
#
# The reply is a permanent 554 rather than a 421 on purpose. A deferral invites
# the sender to come back every few minutes, which is the load you are trying to
# shed; a permanent refusal tells it to stop. That does mean a block covering a
# legitimate sender bounces their mail rather than delaying it, so keep the
# ranges narrow and take them out again afterwards.
PYMTA_BLOCKED_NETWORKS = _env_networks("PYMTA_BLOCKED_NETWORKS")


def _env_lower_set(name: str) -> frozenset[str]:
    """Comma-separated list, lower-cased. Empty entries dropped."""
    return frozenset(
        chunk.strip().lower() for chunk in _env_str(name, "").split(",") if chunk.strip()
    )


# Sender domains refused at MAIL FROM with 554, matched case-insensitively on
# the part after the '@'. A joe-job forging one domain is far more common than a
# netblock attack, and PYMTA_BLOCKED_NETWORKS cannot touch it.
PYMTA_BLOCKED_SENDER_DOMAINS = _env_lower_set("PYMTA_BLOCKED_SENDER_DOMAINS")

# Full recipient addresses refused at RCPT TO with 550, matched
# case-insensitively. For taking one mailbox out of the line of fire without
# touching the MDA.
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

# Accept the dialogue but no mail: every RCPT answers 451. Senders keep the
# message in their own queues and retry for days, so nothing is lost while the
# MDA is broken or an incident is being worked. The alternative today is killing
# the container, which is the same outcome for a well-behaved sender but leaves
# no signal, no metric, and no way back without a deploy.
PYMTA_DEFER_ALL = _env_bool("PYMTA_DEFER_ALL", False)


# None of the settings is re-read while the process runs: a change is a
# restart, which the SIGTERM drain makes graceful. A running process cannot be
# given new environment variables, so a reload from the environment could only
# ever find the values it started with. SIGHUP re-reads the certificate files
# and nothing else (``controller.Listener.reload_tls``).
#
# Future idea: read the crisis toggles above from a mounted file on SIGHUP.
# The code is ready for it in one respect: the handler, the gate and the
# protocol read these values from this module at each use.


# ---------------------------------------------------------------------------
# STARTTLS (opportunistic). When both files are set, STARTTLS is advertised.
#
# Both are comma-separated lists, paired positionally, so a server can hold one
# certificate per key type: OpenSSL keeps a slot per type and picks the one the
# client can actually verify, which is how a single listener serves ECDSA to
# modern senders and RSA to everything else. One entry each is the normal case.
#
# Both lists are required, and must be the same length; see the checks below.
# ---------------------------------------------------------------------------

PYMTA_TLS_CERT_FILE = _env_str("PYMTA_TLS_CERT_FILE", "")
PYMTA_TLS_KEY_FILE = _env_str("PYMTA_TLS_KEY_FILE", "")

# Postfix-style bundle, read only when neither path is set. Postfix packs the
# key and the chain into one PEM, and Python's ssl accepts such a bundle as both
# certfile and keyfile, so each entry stands in for both halves of a pair.
_chain = os.environ.get("STARTTLS_CHAIN_FILES", "").strip()
if _chain and not PYMTA_TLS_CERT_FILE and not PYMTA_TLS_KEY_FILE:
    LEGACY_IN_USE.add(("STARTTLS_CHAIN_FILES", "PYMTA_TLS_CERT_FILE + PYMTA_TLS_KEY_FILE"))
    PYMTA_TLS_CERT_FILE = PYMTA_TLS_KEY_FILE = _chain


def _tls_paths(raw: str, name: str) -> tuple[str, ...]:
    """Split a comma-separated path list, dropping stray empty entries.

    A trailing comma is tolerated because Postfix tolerated it and the value
    may have been written for that image: refusing it would turn a working
    configuration into a process that will not boot, which on an MX means
    inbound mail stops. A value that names *no* path is still refused, since
    the pair check below would read it as "STARTTLS deliberately off" and lose
    transport encryption without a word.
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


# Named for what the operator actually set, so the error above points at the
# variable in their env file rather than at the one the fallback copied into.
_tls_names = ("PYMTA_TLS_CERT_FILE", "PYMTA_TLS_KEY_FILE")
if _chain and PYMTA_TLS_CERT_FILE == PYMTA_TLS_KEY_FILE == _chain:
    _tls_names = ("STARTTLS_CHAIN_FILES", "STARTTLS_CHAIN_FILES")

_certs = _tls_paths(PYMTA_TLS_CERT_FILE, _tls_names[0])
_keys = _tls_paths(PYMTA_TLS_KEY_FILE, _tls_names[1])

# Both or neither. ``load_tls_context`` returns None unless it has a pair, so a
# half-configured STARTTLS would not fail — it would serve plaintext and simply
# not advertise STARTTLS, which is the quietest possible way to lose transport
# encryption. Refuse it here instead.
if bool(_certs) != bool(_keys):
    raise ValueError(
        "PYMTA_TLS_CERT_FILE and PYMTA_TLS_KEY_FILE must be set together; "
        f"only {'PYMTA_TLS_CERT_FILE' if _certs else 'PYMTA_TLS_KEY_FILE'} "
        "is set, which would leave STARTTLS off without saying so. Leave both "
        "empty to disable STARTTLS deliberately."
    )

# Paired positionally, so a mismatch means at least one certificate would be
# loaded against another's key. That fails inside OpenSSL with a message about
# key values, naming neither variable; refuse it here where both are in hand.
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
# Off by default, and meant for a listener whose senders are known (a private
# relay, a partner link). On a public MX it loses the mail of every sender
# that cannot do TLS, or that fails the handshake and retries in clear. To get
# encryption from the senders that can, without losing the others, publish
# MTA-STS or DANE for the domain instead: the sender then enforces it.
PYMTA_REQUIRE_TLS = _env_bool("PYMTA_REQUIRE_TLS", False)
if PYMTA_REQUIRE_TLS and not PYMTA_TLS_CERT_PAIRS:
    raise ValueError(
        "PYMTA_REQUIRE_TLS is set but no certificate is configured "
        "(PYMTA_TLS_CERT_FILE / PYMTA_TLS_KEY_FILE): every message would be refused."
    )

# Oldest TLS version accepted on STARTTLS: 1.0, 1.1, 1.2 or 1.3.
#
# 1.2 is Python's default. A sender that only speaks 1.0 or 1.1 fails the
# handshake, and then either retries in clear or gives up, so on an inbound MX
# an old protocol can be better than none. Going below 1.2 also drops OpenSSL
# to security level 0 for every handshake on this listener, because the
# SHA-1 signatures those versions use are refused above it.
_TLS_VERSIONS = ("1.0", "1.1", "1.2", "1.3")
PYMTA_TLS_MIN_VERSION = _env_str("PYMTA_TLS_MIN_VERSION", "1.2").strip()
if PYMTA_TLS_MIN_VERSION not in _TLS_VERSIONS:
    raise ValueError(
        f"Environment variable PYMTA_TLS_MIN_VERSION is set to {PYMTA_TLS_MIN_VERSION!r}. "
        f"Use one of {', '.join(_TLS_VERSIONS)}."
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

# Bearer token required to scrape /metrics. Empty means no authentication, which
# is what most exporters ship as, and which ``metrics.start_metrics_server``
# warns about at startup. Matches the Django side's PROMETHEUS_API_KEY, opt-in
# semantics included, so both endpoints in this project are scraped alike.
PYMTA_METRICS_API_KEY = _env_str("PYMTA_METRICS_API_KEY", "")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

# Let third-party loggers through at PYMTA_LOG_LEVEL instead of dropping their
# records (see logfmt.OwnRecordsOnly). Debugging aid, and a deliberate
# one: with this on, httpx and httpcore trace every MDA request, headers
# included. Do not leave it set.
PYMTA_LOG_VERBOSE_LIBRARIES = _env_bool("PYMTA_LOG_VERBOSE_LIBRARIES", False)

# One ``session_closed`` line per admitted connection, at INFO: who connected,
# what they said, how it ended. The only record of a sender that never reached
# a message outcome (TLS refused, a command refused for its syntax, a timeout,
# a hang-up). A peer drives its rate: under a connection flood, set it to
# false and the line drops to DEBUG.
PYMTA_LOG_SESSIONS = _env_bool("PYMTA_LOG_SESSIONS", True)

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
