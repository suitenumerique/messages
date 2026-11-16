"""The single DNS entry point used for every record lookup in this codebase.

MX routing, DKIM and ARC key retrieval, SPF and DMARC evaluation and the
mail-domain DNS check all go through :func:`resolve_answer`. Two backends sit
behind it, picked by ``DNS_RESOLVER_BACKEND``:

- ``system``: dnspython's stub resolver, querying whatever
  ``/etc/resolv.conf`` points at. ``Answer.secure`` is always ``False`` here:
  the AD bit of an upstream resolver reached over plain UDP/53 is forgeable,
  so it is never reported as validation.
- ``recursive``: ``recursive_resolver``, which walks the delegation chain from
  the root and validates DNSSEC itself. Needs outbound UDP/TCP 53 over IPv4 to
  arbitrary authoritative nameservers.

The API is ``recursive_resolver``'s: its :class:`Answer`, its
:class:`ValidationState` and its exception hierarchy. The system backend
translates dnspython's results and errors into those, so callers never see
which backend answered. This module is the only place allowed to import either
``dns.resolver`` or ``recursive_resolver`` (ruff banned-api rule).

:func:`resolve_answer_cached` adds an application-level cache in the Django
cache (Redis in production). It is for lookups made on behalf of third-party
senders at inbound volume (SPF, DMARC). Lookups on our own customers' domains
stay uncached so DNS edits show up on the next check.
"""

import contextlib
import contextvars
import functools
import hashlib
import ipaddress
import logging
import time

from django.conf import settings
from django.core.cache import cache

import dns.exception
import dns.name
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import dns.resolver  # noqa: PLW0406 (dnspython's, not this module)
import dns.reversename
import dns.rrset
from recursive_resolver import (
    Answer,
    CNAMELoopError,
    DNSCache,
    DNSSECError,
    DNSSECInsecureError,
    DNSSECMaterialUnavailableError,
    DNSSECUnavailableError,
    DNSSECValidationError,
    InvalidNameError,
    MaxDepthError,
    NoAnswerError,
    NXDOMAINError,
    QueryBudgetExceededError,
    RecursiveResolver,
    ResolutionTimeoutError,
    ResolverError,
    ServfailError,
    UnsupportedRdtypeError,
    ValidationState,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BACKEND_RECURSIVE",
    "BACKEND_SYSTEM",
    "Answer",
    "CNAMELoopError",
    "DNSSECError",
    "DNSSECInsecureError",
    "DNSSECMaterialUnavailableError",
    "DNSSECUnavailableError",
    "DNSSECValidationError",
    "InvalidNameError",
    "MaxDepthError",
    "NXDOMAINError",
    "NoAnswerError",
    "QueryBudgetExceededError",
    "ResolutionTimeoutError",
    "ResolverError",
    "ServfailError",
    "UnsupportedRdtypeError",
    "ValidationState",
    "dns_budget",
    "get_recursive_resolver",
    "get_system_resolver",
    "resolve_answer",
    "resolve_answer_cached",
    "resolve_txt_values",
]

BACKEND_SYSTEM = "system"
BACKEND_RECURSIVE = "recursive"
BACKENDS = (BACKEND_SYSTEM, BACKEND_RECURSIVE)


@functools.cache
def get_recursive_resolver() -> RecursiveResolver:
    """The process-wide recursive resolver.

    Built once and shared: it holds the root delegation cache and the
    validated DNSKEY cache, and is thread-safe with per-key query
    deduplication. Settings are read at first use, so a test varying them
    must call ``get_recursive_resolver.cache_clear()``.

    No record data and no negative answer is cached, and only the root's
    referrals to the TLDs are kept, so a customer's DNS edit or provider move
    is visible on the next lookup. Reuse we want lives in
    :func:`resolve_answer_cached`.
    """
    resolver = RecursiveResolver(
        timeout=settings.DNS_RESOLVER_TIMEOUT,
        max_resolution_time=settings.DNS_RESOLVER_MAX_RESOLUTION_TIME,
        cache_answers=False,
    )
    # The constructor does not expose ``max_negative_ttl``, and
    # ``cache_answers`` does not gate NXDOMAIN / NODATA.
    resolver.cache = DNSCache(max_delegation_depth="tld", max_negative_ttl=0)
    return resolver


@functools.cache
def get_system_resolver() -> dns.resolver.Resolver:
    """The process-wide stub resolver, configured from ``/etc/resolv.conf``.

    ``timeout`` bounds one query to one nameserver and ``lifetime`` the whole
    lookup, mirroring the two recursive settings. dnspython's own cache stays
    off, for the same freshness reason as the recursive backend.
    """
    resolver = dns.resolver.Resolver()
    resolver.timeout = settings.DNS_RESOLVER_TIMEOUT
    resolver.lifetime = settings.DNS_RESOLVER_MAX_RESOLUTION_TIME
    return resolver


def _system_qname(qname: str, rdtype: str) -> dns.name.Name:
    """Validate and build the query name the way ``recursive_resolver`` does."""
    if not isinstance(qname, str) or not qname.strip():
        raise InvalidNameError(str(qname), "empty name")
    if rdtype == "PTR":
        try:
            return dns.reversename.from_address(str(ipaddress.ip_address(qname)))
        except ValueError:
            pass
    try:
        name = dns.name.from_text(qname)
    except dns.name.LabelTooLong as exc:
        raise InvalidNameError(qname, "a label exceeds 63 octets") from exc
    except dns.name.NameTooLong as exc:
        raise InvalidNameError(qname, "name exceeds 255 octets") from exc
    except dns.name.EmptyLabel as exc:
        raise InvalidNameError(qname, "empty label") from exc
    except dns.exception.DNSException as exc:
        raise InvalidNameError(qname, str(exc) or type(exc).__name__) from exc
    except UnicodeError as exc:
        raise InvalidNameError(qname, f"IDNA encoding failed: {exc}") from exc
    return name


def _system_rdtype(rdtype: str) -> dns.rdatatype.RdataType:
    try:
        value = dns.rdatatype.from_text(rdtype)
    except (dns.rdatatype.UnknownRdatatype, ValueError) as exc:
        raise UnsupportedRdtypeError(rdtype) from exc
    if value == dns.rdatatype.ANY:
        raise UnsupportedRdtypeError(rdtype, "ANY is not supported")
    return value


def _system_resolve_answer(
    qname: str, rdtype: str, lifetime: float | None = None
) -> Answer:
    """Resolve through the stub resolver, translated to the shared API.

    ``lifetime`` can only shorten the configured one.
    """
    rdtype = rdtype.strip().upper()
    name = _system_qname(qname, rdtype)
    rdtype_int = _system_rdtype(rdtype)
    resolver = get_system_resolver()
    kwargs = {"search": False}
    if lifetime is not None:
        kwargs["lifetime"] = min(lifetime, resolver.lifetime)
    try:
        result = resolver.resolve(name, rdtype_int, **kwargs)
    except dns.resolver.NXDOMAIN as exc:
        raise NXDOMAINError(qname, rdtype) from exc
    except dns.resolver.NoAnswer as exc:
        raise NoAnswerError(qname, rdtype) from exc
    except dns.resolver.LifetimeTimeout as exc:
        raise ResolutionTimeoutError(qname, rdtype) from exc
    except dns.resolver.NoNameservers as exc:
        raise ServfailError(qname, rdtype) from exc
    except dns.resolver.YXDOMAIN as exc:
        raise InvalidNameError(qname, "DNAME substitution overflows") from exc
    except dns.exception.DNSException as exc:
        raise ResolverError(f"{type(exc).__name__}: {exc}", qname, rdtype) from exc
    return Answer(
        qname=name,
        canonical_name=result.canonical_name,
        rdtype=rdtype_int,
        rrset=result.rrset,
        # The answer is only valid while every CNAME leading to it is.
        ttl=result.chaining_result.minimum_ttl,
        dnssec=ValidationState.DISABLED,
    )


# Deadline (``time.monotonic()``) shared by every lookup in the current
# context, set by :func:`dns_budget`.
_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "dns_deadline", default=None
)


@contextlib.contextmanager
def dns_budget(seconds: float):
    """Bound the total time lookups in this block may start within.

    Past the deadline a lookup raises ``ResolutionTimeoutError`` without
    querying. One already running still finishes (the system backend's is
    cut to what is left, the recursive one keeps its own bound), so the block
    can overrun by at most one ``DNS_RESOLVER_MAX_RESOLUTION_TIME``. Nested
    budgets keep the earlier deadline.
    """
    deadline = time.monotonic() + seconds
    outer = _deadline.get()
    if outer is not None:
        deadline = min(deadline, outer)
    token = _deadline.set(deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def _remaining() -> float | None:
    deadline = _deadline.get()
    return None if deadline is None else deadline - time.monotonic()


def resolve_answer(qname: str, rdtype: str = "A") -> Answer:
    """Resolve one name, returning the rrset plus its DNSSEC state.

    For PTR, ``qname`` may be an IP address; it is reversed for you.
    """
    remaining = _remaining()
    if remaining is not None and remaining <= 0:
        raise ResolutionTimeoutError(str(qname), str(rdtype))
    if settings.DNS_RESOLVER_BACKEND == BACKEND_RECURSIVE:
        return get_recursive_resolver().resolve_answer(qname, rdtype)
    return _system_resolve_answer(qname, rdtype, lifetime=remaining)


def resolve_txt_values(qname: str) -> list[str]:
    """TXT values for a name, one string per record.

    ``Answer.text_values`` joins each record's ``<character-string>`` chunks
    with no separator, which is what RFC 6376 3.6.2.2 (DKIM) and RFC 7208
    3.3 (SPF) require.
    """
    return resolve_answer(qname, "TXT").text_values()


# Cached outcomes. A lookup that did not complete is only kept for
# ``DNS_RESOLVER_CACHE_FAILURE_TTL`` (RFC 9520 3.2), so a slow nameserver does
# not cost a full timeout on every message.
_CACHED_ANSWER = "answer"
_CACHED_NXDOMAIN = "nxdomain"
_CACHED_NODATA = "nodata"
_CACHED_SERVFAIL = "servfail"
_CACHED_TIMEOUT = "timeout"


def _cache_key(qname: str, rdtype: str) -> str:
    # Hashed: the name may come from attacker-controlled SPF macros.
    digest = hashlib.sha256(qname.strip().rstrip(".").lower().encode()).hexdigest()
    return f"dns:{settings.DNS_RESOLVER_BACKEND}:{rdtype}:{digest}"


def _answer_from_cache(entry, qname: str, rdtype: str) -> Answer:
    kind, payload = entry
    if kind == _CACHED_NXDOMAIN:
        raise NXDOMAINError(qname, rdtype)
    if kind == _CACHED_NODATA:
        raise NoAnswerError(qname, rdtype)
    if kind == _CACHED_SERVFAIL:
        raise ServfailError(qname, rdtype)
    if kind == _CACHED_TIMEOUT:
        raise ResolutionTimeoutError(qname, rdtype)
    owner, canonical, ttl, dnssec, rdatas = payload
    rdtype_int = dns.rdatatype.from_text(rdtype)
    wires = [bytes.fromhex(rdata) for rdata in rdatas]
    rrset = dns.rrset.from_rdata_list(
        dns.name.from_text(canonical),
        ttl,
        [
            dns.rdata.from_wire(dns.rdataclass.IN, rdtype_int, wire, 0, len(wire))
            for wire in wires
        ],
    )
    return Answer(
        qname=dns.name.from_text(owner),
        canonical_name=rrset.name,
        rdtype=rdtype_int,
        rrset=rrset,
        ttl=ttl,
        dnssec=ValidationState(dnssec),
    )


def resolve_answer_cached(qname: str, rdtype: str = "A") -> Answer:
    """:func:`resolve_answer` behind a short-lived shared cache.

    Positive answers are kept for their TTL, capped at
    ``DNS_RESOLVER_CACHE_MAX_TTL``. NXDOMAIN and NODATA are kept for
    ``DNS_RESOLVER_CACHE_NEGATIVE_TTL``, SERVFAIL and timeouts for
    ``DNS_RESOLVER_CACHE_FAILURE_TTL``, except a timeout cut short by
    :func:`dns_budget`: that says nothing about the nameserver. Anything else
    (DNSSEC failure, other errors) is not stored. A cache outage degrades to
    an uncached lookup.
    """
    rdtype = rdtype.strip().upper()
    key = _cache_key(qname, rdtype)
    max_ttl = settings.DNS_RESOLVER_CACHE_MAX_TTL
    if max_ttl <= 0:
        return resolve_answer(qname, rdtype)
    try:
        entry = cache.get(key)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("DNS cache read failed: %s", exc)
        entry = None
    if entry is not None:
        try:
            return _answer_from_cache(entry, qname, rdtype)
        except (
            NXDOMAINError,
            NoAnswerError,
            ServfailError,
            ResolutionTimeoutError,
        ):
            raise
        # pylint: disable-next=broad-exception-caught
        except Exception as exc:
            # An entry written by another version, or corrupted: a miss.
            logger.warning("Ignoring unreadable DNS cache entry: %s", exc)

    remaining = _remaining()
    try:
        answer = resolve_answer(qname, rdtype)
    except NXDOMAINError:
        _cache_set(key, (_CACHED_NXDOMAIN, None), _negative_ttl())
        raise
    except NoAnswerError:
        _cache_set(key, (_CACHED_NODATA, None), _negative_ttl())
        raise
    except ServfailError:
        _cache_set(key, (_CACHED_SERVFAIL, None), _failure_ttl())
        raise
    except ResolutionTimeoutError:
        if remaining is None or remaining >= settings.DNS_RESOLVER_MAX_RESOLUTION_TIME:
            _cache_set(key, (_CACHED_TIMEOUT, None), _failure_ttl())
        raise

    ttl = min(answer.ttl, max_ttl)
    if ttl > 0:
        payload = (
            answer.qname.to_text(),
            answer.rrset.name.to_text(),
            answer.ttl,
            answer.dnssec.value,
            [rdata.to_wire().hex() for rdata in answer.rrset],
        )
        _cache_set(key, (_CACHED_ANSWER, payload), ttl)
    return answer


def _negative_ttl() -> int:
    return min(
        settings.DNS_RESOLVER_CACHE_NEGATIVE_TTL, settings.DNS_RESOLVER_CACHE_MAX_TTL
    )


def _failure_ttl() -> int:
    return min(
        settings.DNS_RESOLVER_CACHE_FAILURE_TTL, settings.DNS_RESOLVER_CACHE_MAX_TTL
    )


def _cache_set(key: str, value, ttl: int) -> None:
    if ttl <= 0:
        return
    try:
        cache.set(key, value, ttl)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("DNS cache write failed: %s", exc)
