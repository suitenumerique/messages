"""The single DNS resolver used for every record lookup in this codebase.

MX routing, DKIM and ARC key retrieval, SPF include chains and the mail-domain
DNS check all go through :func:`get_resolver`. It walks the delegation chain
from the root servers and validates DNSSEC itself, so :attr:`Answer.secure` is
something we established rather than an AD bit asserted by whatever
``/etc/resolv.conf`` points at — over plain UDP/53 that bit is forgeable, which
is the whole reason a "require DNSSEC" switch built on it bought so little.

This needs outbound UDP/TCP 53 over IPv4 to arbitrary authoritative
nameservers — the resolver contacts them over IPv4 only. There is no
stub-resolver fallback: falling back would drop the validation guarantee
precisely when the network is being interfered with.

This module is also the only place allowed to import ``recursive_resolver``
(enforced by a ruff banned-api rule). Call sites take the answer types and the
error classes from here, so swapping in another resolver implementation stays a
change to this file rather than to every caller.
"""

import functools

from django.conf import settings

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

__all__ = [
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
    "RecursiveResolver",
    "ResolutionTimeoutError",
    "ResolverError",
    "ServfailError",
    "UnsupportedRdtypeError",
    "ValidationState",
    "get_resolver",
    "resolve_answer",
    "resolve_txt_values",
]


@functools.cache
def get_resolver() -> RecursiveResolver:
    """The process-wide resolver.

    Built once, and deliberately shared: it holds the root delegation cache
    and the validated DNSKEY cache, and is thread-safe with per-key query
    deduplication, so a resolver per call site would mean every one of them
    walking from the root again.

    The settings below are therefore read once, at first use: a test that
    needs to vary them has to call ``get_resolver.cache_clear()`` inside its
    ``override_settings`` block, and again on the way out so the next test
    does not inherit the override's resolver.

    **Caching policy: everything a customer can change is read live.** People
    edit their DNS and expect the next check to reflect it, and a domain
    registered a minute ago has to work now, so we hold nothing that could
    answer from before the change:

    - ``cache_answers=False`` — no record data is cached. A published or
      corrected TXT record takes effect on the next lookup, not after a TTL
      we chose on the publisher's behalf.
    - ``max_negative_ttl=0`` — negative answers expire on write. This is a
      separate switch because ``cache_answers`` does not gate NXDOMAIN and
      NODATA, and a cached "no such record" is precisely what would keep
      telling a customer their new record is missing.
    - ``max_delegation_depth="tld"`` — only the root's referrals to the TLDs
      are kept, for their real TTL. Every cut below that is re-resolved, so a
      domain moving to a new DNS provider is followed immediately instead of
      being read out of an NS set cached for the two days its TTL asks for —
      which would have us reading the old provider's zone and reporting the
      customer's new records as missing.

    What that leaves cached is the one hop we must not re-query at volume:
    root servers are a shared resource, and the root's delegations are also
    the part of the chain that never moves. The cost is one extra query to a
    TLD server per lookup, which is a deliberate trade — TLD servers are
    provisioned for exactly this, and correctness beats saving a round trip.

    Reuse we do want belongs at the application layer, where it is shared
    across processes and we control invalidation (as ``check_spf_status``
    already does), not in a per-process cache that answers with data the
    customer has since replaced.
    """
    resolver = RecursiveResolver(
        timeout=settings.DNS_RESOLVER_TIMEOUT,
        max_resolution_time=settings.DNS_RESOLVER_MAX_RESOLUTION_TIME,
        cache_answers=False,
    )
    # Replaces the cache the constructor built: it does not expose
    # ``max_negative_ttl``, and negative caching is the half of freshness
    # ``cache_answers`` leaves on.
    resolver.cache = DNSCache(max_delegation_depth="tld", max_negative_ttl=0)
    return resolver


def resolve_answer(qname: str, rdtype: str = "A") -> Answer:
    """Resolve one name, returning the rrset plus its DNSSEC state."""
    return get_resolver().resolve_answer(qname, rdtype)


def resolve_txt_values(qname: str) -> list[str]:
    """TXT values for a name, one string per record.

    ``Answer.text_values`` joins each record's ``<character-string>`` chunks
    with no separator, which is what RFC 6376 §3.6.2.2 (DKIM) and RFC 7208
    §3.3 (SPF) require. A 2048-bit DKIM key is always split in two, so a
    consumer that reads presentation format and strips quotes gets a key that
    never verifies.
    """
    return resolve_answer(qname, "TXT").text_values()
