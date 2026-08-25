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


# Postfix-image variables that were read because their pymta counterpart was
# unset. Reported once at startup so a migration finishes rather than stalling
# on defaults nobody restated.
LEGACY_IN_USE: set[tuple[str, str]] = set()


def _env_name(name: str, legacy: str) -> str:
    """Which variable to read: the prefixed one, or the Postfix image's.

    Both images can run from one env file during a switchover, so the old names
    keep working. The prefixed name always wins when set; the old one is only
    consulted in its absence, and using it is recorded so startup can say so.
    """
    if not os.environ.get(name, "").strip() and os.environ.get(legacy, "").strip():
        # A set, not a list: reload_runtime_settings re-reads these on every
        # SIGHUP, so appending would grow without bound and report the same
        # variable once per reload.
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
# don't broadcast "aiosmtpd X.Y.Z" to internet scanners.
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
# which on the defaults is 1010 commands before aiosmtpd force-closes. Raising
# either of these raises that ceiling as their product, and it is the ceiling
# on how many commands one connection can spend re-arming the idle timer.
PYMTA_MAX_RECIPIENTS_PER_ENVELOPE = _env_int("PYMTA_MAX_RECIPIENTS_PER_ENVELOPE", 100, minimum=1)

# Envelopes per TCP connection (one envelope = MAIL FROM..DATA cycle).
PYMTA_MAX_ENVELOPES_PER_SESSION = _env_int("PYMTA_MAX_ENVELOPES_PER_SESSION", 10, minimum=1)

# Longest single line accepted in DATA or a command, in octets.
#
# aiosmtpd defaults to 1001 (RFC 5321 §4.5.3.1.6) and answers a longer line with
# ``500 Line too long``, a permanent rejection. Postfix accepts the same
# message: its ``line_length_limit`` is 2048 and applies to how the cleanup
# daemon wraps output, not to what smtpd will receive. Long unwrapped HTML and
# URLs break 998 constantly, so the strict value bounces mail that lands today,
# and 5xx means the sender gives up rather than retrying.
#
# 64 KiB accepts effectively all real mail while still bounding the per-session
# read buffer (this is the ``StreamReader`` limit, so the ceiling is this value
# times PYMTA_MAX_SESSIONS_TOTAL). It does not weaken command validation:
# aiosmtpd checks commands against its own 512-octet ``command_size_limit``
# after the line is read.
PYMTA_MAX_LINE_LENGTH = _env_int("PYMTA_MAX_LINE_LENGTH", 65536, minimum=1001)

# Messages that may be in memory at once. 0 disables the bound.
#
# aiosmtpd holds each message in RAM, so this is what stands between a burst of
# large mail and the OOM killer. The session caps do not: a connection costs a
# few kB until it says DATA.
#
# Size it against the memory the container has. A message peaks at roughly 2.2x
# its size, because aiosmtpd needs a second copy while it joins the received
# lines and freed arenas are not returned promptly, so 40 x 10 MiB is about
# 880 MiB and wants a container of 1 GiB or more. Drop it to 20 for 512 MiB.
#
# It is also the number of distinct hosts it takes to saturate delivery, because
# the gate shares the slots among the sources present rather than by a fixed
# per-source cap. More memory buys throughput and that resistance together.
PYMTA_MAX_CONCURRENT_DATA = _env_int("PYMTA_MAX_CONCURRENT_DATA", 40, minimum=0)

# RFC 5321 §4.5.3.1.1/.1.2: local-part ≤ 64 octets, domain ≤ 255 octets.
# Constants, not env vars: these are the protocol's numbers, not a deployment
# choice, and raising them would only widen the gap between what pymta accepts
# at RCPT and what the MDA can store. ``validate_envelope_address`` still takes
# them as arguments so the tests can probe the boundaries directly.
PYMTA_MAX_LOCAL_PART = 64
PYMTA_MAX_DOMAIN = 255


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
# ``handler._REPLY_RESERVE_SECONDS``. Not configurable: it is a property of how
# the two deadlines nest, not something an operator sizes. It bounds the DATA timeout
# from below, because a budget at or under the reserve leaves the deliver call
# nothing at all and would defer every message.
PYMTA_REPLY_RESERVE_SECONDS = 10

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
_SESSIONS_PER_DATA_SLOT = 3
PYMTA_MAX_SESSIONS_TOTAL = PYMTA_MAX_CONCURRENT_DATA * _SESSIONS_PER_DATA_SLOT

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


# Comma-separated IPs / CIDRs allowed to send a PROXY header, matched against
# the *wire* peer (the TCP source, i.e. the load balancer). Anything the header
# claims is only as trustworthy as the peer that sent it: the claimed source IP
# becomes the key for every per-IP cap and the ``client_address`` the MDA bakes
# into the Received header. A peer that reaches port 25 directly could
# otherwise scatter a forged source across the address space (defeating
# the per-source shares) and attribute its mail to any IP it
# likes.
#
# Strongly recommended whenever PROXY protocol is enabled, because enabling it
# *is* the claim that a balancer sits in front, so the balancer's address is
# usually a known fact. Left empty there is nothing to match on and every peer's
# header is trusted; ``server.py`` warns loudly at startup rather than refusing
# to run, since some deployments only learn the balancer's addresses later.
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


PYMTA_TRUSTED_PROXIES = _env_networks("PYMTA_TRUSTED_PROXIES")


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


# ---------------------------------------------------------------------------
# Crisis toggles
# ---------------------------------------------------------------------------

# Accept the dialogue but no mail: every RCPT answers 451. Senders keep the
# message in their own queues and retry for days, so nothing is lost while the
# MDA is broken or an incident is being worked. The alternative today is killing
# the container, which is the same outcome for a well-behaved sender but leaves
# no signal, no metric, and no way back without a deploy.
PYMTA_DEFER_ALL = _env_bool("PYMTA_DEFER_ALL", False)

# Take this node out of rotation: answer 421 at connect, before the banner, so
# senders move to another MX or come back later. Distinct from PYMTA_DEFER_ALL,
# which keeps talking. Reversible, unlike closing the listener.
PYMTA_DRAIN = _env_bool("PYMTA_DRAIN", False)


# Settings re-read on SIGHUP: everything consulted per connection, per command
# or per shutdown, which is what makes it safe to swap under a running process.
#
# Excluded, and why, because "it looked hard" is not a reason:
#
#   PYMTA_COMMAND_TIMEOUT, PYMTA_SMTP_*, PYMTA_METRICS_*, PYMTA_TLS_*,
#   PYMTA_ENABLE_*, PYMTA_MAX_LINE_LENGTH, MDA_API_*, MDA_BREAKER_*
#       captured when the SMTP kwargs, the listener socket, the TLS context,
#       the protocol class or the HTTP client are built. Re-reading them would
#       change this module and not the server, which is worse than not
#       offering it.
#
#   PYMTA_MAX_CONCURRENT_DATA is included because the gate reads it on every
#   DATA command, which makes shedding memory pressure a SIGHUP rather than a
#   restart. PYMTA_MAX_SESSIONS_TOTAL follows it, being derived from it.
#
#   PYMTA_MAX_INCOMING_EMAIL_SIZE, PYMTA_MAX_RECIPIENTS_PER_ENVELOPE,
#   PYMTA_MAX_ENVELOPES_PER_SESSION
#       read live by the handler, but ALSO baked into aiosmtpd's
#       ``data_size_limit`` and ``command_call_limit`` at construction. Lowering
#       one would work; raising it would not, because the captured ceiling cuts
#       in first. A setting that silently only moves in one direction is worse
#       than one that does not move, so they stay out until the two halves are
#       unified.
_RELOADABLE = (
    "PYMTA_DEFER_ALL",
    "PYMTA_DRAIN",
    "PYMTA_MAX_CONCURRENT_DATA",
    "PYMTA_MAX_SESSIONS_TOTAL",
    "PYMTA_BLOCKED_NETWORKS",
    "PYMTA_BLOCKED_SENDER_DOMAINS",
    "PYMTA_BLOCKED_RECIPIENTS",
    "PYMTA_MAX_ERRORS_PER_SESSION",
    "PYMTA_MAX_RCPT_MISSES_PER_SESSION",
    "PYMTA_DATA_TIMEOUT",
    "PYMTA_SESSION_TIMEOUT",
    "PYMTA_SHUTDOWN_TIMEOUT",
)


def reload_runtime_settings() -> dict[str, object]:
    """Re-read the reloadable settings from the environment. Returns what changed.

    Parsed into locals and validated in full *before* anything is rebound, so a
    typo introduced under pressure leaves the running configuration untouched
    rather than half-applied. The caller logs and carries on.
    """
    fresh = {
        "PYMTA_DEFER_ALL": _env_bool("PYMTA_DEFER_ALL", False),
        "PYMTA_DRAIN": _env_bool("PYMTA_DRAIN", False),
        "PYMTA_MAX_CONCURRENT_DATA": _env_int("PYMTA_MAX_CONCURRENT_DATA", 40, minimum=0),
        "PYMTA_BLOCKED_NETWORKS": _env_networks("PYMTA_BLOCKED_NETWORKS"),
        "PYMTA_BLOCKED_SENDER_DOMAINS": _env_lower_set("PYMTA_BLOCKED_SENDER_DOMAINS"),
        "PYMTA_BLOCKED_RECIPIENTS": _env_lower_set("PYMTA_BLOCKED_RECIPIENTS"),
        "PYMTA_MAX_ERRORS_PER_SESSION": _env_int("PYMTA_MAX_ERRORS_PER_SESSION", 50, minimum=1),
        "PYMTA_MAX_RCPT_MISSES_PER_SESSION": _env_int(
            "PYMTA_MAX_RCPT_MISSES_PER_SESSION", 10, minimum=1
        ),
        "PYMTA_DATA_TIMEOUT": _env_int(
            "PYMTA_DATA_TIMEOUT", 300, minimum=PYMTA_REPLY_RESERVE_SECONDS + 1
        ),
        "PYMTA_SESSION_TIMEOUT": _env_int("PYMTA_SESSION_TIMEOUT", 1800, minimum=0),
        "PYMTA_SHUTDOWN_TIMEOUT": _env_int("PYMTA_SHUTDOWN_TIMEOUT", 25, minimum=0),
    }
    # Derived, so recomputed rather than read: reloading the message cap without
    # it would leave the session cap describing the old one.
    fresh["PYMTA_MAX_SESSIONS_TOTAL"] = (
        fresh["PYMTA_MAX_CONCURRENT_DATA"] * _SESSIONS_PER_DATA_SLOT
    )
    changed = {k: v for k, v in fresh.items() if globals()[k] != v}
    globals().update(fresh)
    return changed


# ---------------------------------------------------------------------------
# STARTTLS (opportunistic). When both files are set, STARTTLS is advertised.
#
# Both paths are required together; see the check below.
# ---------------------------------------------------------------------------

PYMTA_TLS_CERT_FILE = _env_str("PYMTA_TLS_CERT_FILE", "")
PYMTA_TLS_KEY_FILE = _env_str("PYMTA_TLS_KEY_FILE", "")

# Postfix-style bundle, read only when neither path is set. Postfix packs the
# key and the chain into one PEM and takes a comma-separated list for RSA+ECDSA
# dual certs; Python's ssl accepts such a bundle as both certfile and keyfile,
# so the first entry is used for each and SNI is left for later if wanted.
_chain = os.environ.get("STARTTLS_CHAIN_FILES", "").strip()
if _chain and not PYMTA_TLS_CERT_FILE and not PYMTA_TLS_KEY_FILE:
    LEGACY_IN_USE.add(("STARTTLS_CHAIN_FILES", "PYMTA_TLS_CERT_FILE + PYMTA_TLS_KEY_FILE"))
    _first = _chain.split(",", 1)[0].strip()
    # A leading comma would otherwise make both paths empty, which reads as
    # "STARTTLS deliberately off" to the pair check below and disables
    # encryption without a word. Same posture as that check: refuse it.
    if not _first:
        raise ValueError(
            "Environment variable STARTTLS_CHAIN_FILES is set to "
            f"{_chain!r}, whose first entry is empty. Give the bundle path "
            "first, or set PYMTA_TLS_CERT_FILE and PYMTA_TLS_KEY_FILE instead."
        )
    PYMTA_TLS_CERT_FILE = PYMTA_TLS_KEY_FILE = _first

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

# Bearer token required to scrape /metrics. Empty means no authentication, which
# is what most exporters ship as, and which ``metrics.start_metrics_server``
# warns about at startup. Matches the Django side's PROMETHEUS_API_KEY, opt-in
# semantics included, so both endpoints in this project are scraped alike.
PYMTA_METRICS_API_KEY = _env_str("PYMTA_METRICS_API_KEY", "")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

# Let the noisy third-party loggers through at PYMTA_LOG_LEVEL instead of their
# pinned levels (see logfmt._LIBRARY_LEVELS). Debugging aid, and a deliberate
# one: with this on, aiosmtpd writes every envelope address and the full body of
# every message into the log. Do not leave it set.
PYMTA_LOG_VERBOSE_LIBRARIES = _env_bool("PYMTA_LOG_VERBOSE_LIBRARIES", False)

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
