"""Tests for the DNS resolver facade (both backends and the shared cache).

Every other DNS test patches its call site, so nothing else exercises this
module: a wrong settings name or constructor keyword here would only surface
in production, where the failure is "no DNS at all".
"""

# ruff: noqa: TID251  (exercises the system backend's dnspython translation)

from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings

import dns.exception
import dns.message
import dns.name
import dns.rdataclass
import dns.rdatatype
import dns.resolver
import pytest

from core.services.dns import resolver as dns_resolver
from core.services.dns.resolver import (
    DNSSECError,
    InvalidNameError,
    NoAnswerError,
    NXDOMAINError,
    RecursiveResolver,
    ResolutionTimeoutError,
    ResolverError,
    ServfailError,
    UnsupportedRdtypeError,
    ValidationState,
)
from core.tests.dns_answers import a_answer, mx_answer, txt_answer


@pytest.fixture(autouse=True)
def _fresh_resolvers():
    """Drop the process-wide instances so settings are read again."""
    dns_resolver.get_recursive_resolver.cache_clear()
    dns_resolver.get_system_resolver.cache_clear()
    cache.clear()
    yield
    dns_resolver.get_recursive_resolver.cache_clear()
    dns_resolver.get_system_resolver.cache_clear()
    cache.clear()


def _dnspython_answer(answer):
    """The shape ``dns.resolver.Resolver.resolve`` returns, from a test Answer."""
    return SimpleNamespace(
        rrset=answer.rrset,
        canonical_name=answer.canonical_name,
        chaining_result=SimpleNamespace(minimum_ttl=answer.rrset.ttl),
    )


# --- Recursive backend -----------------------------------------------------


def test_get_recursive_resolver_builds_from_settings():
    """The settings names and constructor keywords line up."""
    with override_settings(
        DNS_RESOLVER_TIMEOUT=1.5, DNS_RESOLVER_MAX_RESOLUTION_TIME=7.5
    ):
        built = dns_resolver.get_recursive_resolver()
        assert isinstance(built, RecursiveResolver)
        assert built.timeout == 1.5
        assert built.max_resolution_time == 7.5


def test_get_recursive_resolver_is_shared():
    """One instance per process, so the delegation cache is actually reused."""
    assert dns_resolver.get_recursive_resolver() is (
        dns_resolver.get_recursive_resolver()
    )


def test_recursive_record_data_is_never_cached():
    """A customer's published record must take effect on the next lookup.

    ``cache_answers`` does not gate NXDOMAIN/NODATA, so the negative TTL has
    to be zeroed separately.
    """
    built = dns_resolver.get_recursive_resolver()
    assert built.cache_answers is False
    assert built.cache.max_negative_ttl == 0


def test_recursive_only_the_roots_delegations_are_cached():
    """Depth counts root=0, ``com.``=1: 1 is "the root's referrals only"."""
    assert dns_resolver.get_recursive_resolver().cache.max_delegation_depth == 1


@override_settings(DNS_RESOLVER_BACKEND="recursive")
def test_recursive_backend_is_used_when_selected():
    """The toggle routes lookups to the recursive resolver."""
    with (
        patch.object(dns_resolver, "get_recursive_resolver") as mock_recursive,
        patch.object(dns_resolver, "get_system_resolver") as mock_system,
    ):
        mock_recursive.return_value.resolve_answer.return_value = txt_answer(
            "v=spf1 -all", "x" * 300
        )
        values = dns_resolver.resolve_txt_values("example.com")

    # The 300-character value was split across two character-strings by the
    # builder and comes back whole.
    assert values == ["v=spf1 -all", "x" * 300]
    mock_recursive.return_value.resolve_answer.assert_called_once_with(
        "example.com", "TXT"
    )
    mock_system.assert_not_called()


@override_settings(DNS_RESOLVER_BACKEND="recursive")
def test_resolve_answer_defaults_to_a_records():
    """``rdtype`` defaults to A, matching the hostname lookups that omit it."""
    with patch.object(dns_resolver, "get_recursive_resolver") as mock_get:
        dns_resolver.resolve_answer("mx1.example.com")

    mock_get.return_value.resolve_answer.assert_called_once_with("mx1.example.com", "A")


# --- System backend --------------------------------------------------------


def test_system_backend_is_the_default():
    """Out of the box nothing walks from the root."""
    with (
        patch.object(dns_resolver, "get_recursive_resolver") as mock_recursive,
        patch.object(dns_resolver, "get_system_resolver") as mock_system,
    ):
        mock_system.return_value.resolve.return_value = _dnspython_answer(
            a_answer("192.0.2.1", name="mx1.example.com")
        )
        answer = dns_resolver.resolve_answer("mx1.example.com")

    assert answer.records == ["192.0.2.1"]
    mock_recursive.assert_not_called()


def test_get_system_resolver_builds_from_settings():
    """The two timeout settings map onto dnspython's timeout and lifetime."""
    with override_settings(
        DNS_RESOLVER_TIMEOUT=1.5, DNS_RESOLVER_MAX_RESOLUTION_TIME=7.5
    ):
        built = dns_resolver.get_system_resolver()
    assert built.timeout == 1.5
    assert built.lifetime == 7.5


def test_system_answer_is_never_reported_secure():
    """An upstream AD bit over plain UDP is forgeable, so it is not trusted."""
    with patch.object(dns_resolver, "get_system_resolver") as mock_system:
        mock_system.return_value.resolve.return_value = _dnspython_answer(
            txt_answer("v=spf1 -all", secure=True)
        )
        answer = dns_resolver.resolve_answer("example.com", "TXT")

    assert answer.dnssec is ValidationState.DISABLED
    assert answer.secure is False
    assert answer.ttl == 300
    assert answer.text_values() == ["v=spf1 -all"]


def test_system_answer_ttl_is_the_cname_chain_minimum():
    """A short-lived alias must not be cached for its target's TTL."""
    response = dns.message.from_text(
        "id 1\nopcode QUERY\nrcode NOERROR\nflags QR RD RA\n"
        ";QUESTION\nalias.example.com. IN TXT\n"
        ";ANSWER\nalias.example.com. 5 IN CNAME target.example.com.\n"
        'target.example.com. 3600 IN TXT "v=spf1 -all"\n'
    )
    result = dns.resolver.Answer(
        dns.name.from_text("alias.example.com"),
        dns.rdatatype.TXT,
        dns.rdataclass.IN,
        response,
    )
    with patch.object(dns_resolver, "get_system_resolver") as mock_system:
        mock_system.return_value.resolve.return_value = result
        answer = dns_resolver.resolve_answer("alias.example.com", "TXT")

    assert answer.ttl == 5
    assert answer.text_values() == ["v=spf1 -all"]


def test_system_backend_queries_without_search_domains():
    """A relative name must never pick up the host's resolv.conf search list."""
    with patch.object(dns_resolver, "get_system_resolver") as mock_system:
        mock_system.return_value.resolve.return_value = _dnspython_answer(
            mx_answer((10, "mx.example.com."))
        )
        dns_resolver.resolve_answer("example.com", "mx")

    args, kwargs = mock_system.return_value.resolve.call_args
    assert args == (dns.name.from_text("example.com"), dns.rdatatype.MX)
    assert kwargs == {"search": False}


def test_system_backend_reverses_ip_for_ptr():
    """PTR accepts an IP address, as the recursive backend does."""
    with patch.object(dns_resolver, "get_system_resolver") as mock_system:
        mock_system.return_value.resolve.return_value = _dnspython_answer(
            txt_answer("unused")
        )
        dns_resolver.resolve_answer("192.0.2.1", "PTR")

    args, _ = mock_system.return_value.resolve.call_args
    assert args[0] == dns.name.from_text("1.2.0.192.in-addr.arpa")


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (dns.resolver.NXDOMAIN(), NXDOMAINError),
        (dns.resolver.NoAnswer(), NoAnswerError),
        (dns.resolver.LifetimeTimeout(timeout=1.0, errors=[]), ResolutionTimeoutError),
        (dns.resolver.NoNameservers(), ServfailError),
        (dns.resolver.YXDOMAIN(), InvalidNameError),
        (dns.exception.FormError(), ResolverError),
    ],
)
def test_system_backend_translates_errors(raised, expected):
    """Callers only ever see the recursive resolver's exception classes."""
    with patch.object(dns_resolver, "get_system_resolver") as mock_system:
        mock_system.return_value.resolve.side_effect = raised
        with pytest.raises(expected):
            dns_resolver.resolve_answer("example.com", "TXT")


@pytest.mark.parametrize(
    "qname", ["", "a" * 64 + ".example.com", "a..example.com", "x." * 130]
)
def test_system_backend_rejects_invalid_names(qname):
    """Invalid names fail before any query, like the recursive backend."""
    with patch.object(dns_resolver, "get_system_resolver") as mock_system:
        with pytest.raises(InvalidNameError):
            dns_resolver.resolve_answer(qname, "TXT")
    mock_system.return_value.resolve.assert_not_called()


@pytest.mark.parametrize("rdtype", ["NOPE", "ANY"])
def test_system_backend_rejects_unsupported_rdtypes(rdtype):
    with pytest.raises(UnsupportedRdtypeError):
        dns_resolver.resolve_answer("example.com", rdtype)


# --- Budget ----------------------------------------------------------------


def test_spent_budget_refuses_lookups_without_querying():
    with patch.object(dns_resolver, "get_system_resolver") as mock_system:
        with dns_resolver.dns_budget(-1):
            with pytest.raises(ResolutionTimeoutError):
                dns_resolver.resolve_answer("example.com", "TXT")
    mock_system.return_value.resolve.assert_not_called()


@override_settings(DNS_RESOLVER_BACKEND="recursive")
def test_spent_budget_applies_to_the_recursive_backend():
    with patch.object(dns_resolver, "get_recursive_resolver") as mock_recursive:
        with dns_resolver.dns_budget(-1):
            with pytest.raises(ResolutionTimeoutError):
                dns_resolver.resolve_answer("example.com", "TXT")
    mock_recursive.return_value.resolve_answer.assert_not_called()


def test_budget_shortens_the_system_lifetime():
    with patch.object(dns_resolver, "get_system_resolver") as mock_system:
        mock_system.return_value.lifetime = 15.0
        mock_system.return_value.resolve.return_value = _dnspython_answer(
            txt_answer("v=spf1 -all")
        )
        with dns_resolver.dns_budget(2):
            dns_resolver.resolve_answer("example.com", "TXT")
    lifetime = mock_system.return_value.resolve.call_args.kwargs["lifetime"]
    assert 0 < lifetime <= 2


def test_nested_budget_keeps_the_earlier_deadline():
    with dns_resolver.dns_budget(-1):
        with dns_resolver.dns_budget(60):
            with pytest.raises(ResolutionTimeoutError):
                dns_resolver.resolve_answer("example.com", "TXT")


def test_budget_does_not_outlive_its_block():
    with dns_resolver.dns_budget(-1):
        pass
    assert dns_resolver._remaining() is None


# --- Shared cache ----------------------------------------------------------


def test_cached_answer_is_reused():
    """A second lookup within the TTL does not query DNS again."""
    with patch.object(dns_resolver, "_resolve") as mock_resolve:
        mock_resolve.return_value = txt_answer("v=spf1 -all", "x" * 300)
        first = dns_resolver.resolve_answer_cached("example.com", "TXT")
        second = dns_resolver.resolve_answer_cached("EXAMPLE.com.", "TXT")

    assert mock_resolve.call_count == 1
    assert first.text_values() == second.text_values() == ["v=spf1 -all", "x" * 300]
    assert second.ttl == 300


def test_cached_mx_round_trips():
    """Rdata with embedded names survives the cache."""
    with patch.object(dns_resolver, "_resolve") as mock_resolve:
        mock_resolve.return_value = mx_answer((10, "mx.example.com."))
        dns_resolver.resolve_answer_cached("example.com", "MX")
        again = dns_resolver.resolve_answer_cached("example.com", "MX")

    assert [(r.preference, r.exchange.to_text()) for r in again.rrset] == [
        (10, "mx.example.com.")
    ]


@pytest.mark.parametrize(
    "error", [NXDOMAINError("example.com"), NoAnswerError("example.com", "TXT")]
)
def test_cached_negative_answer_is_reused(error):
    """NXDOMAIN and NODATA are settled, so they are cached too."""
    with patch.object(dns_resolver, "_resolve", side_effect=error) as mock:
        for _ in range(2):
            with pytest.raises(type(error)):
                dns_resolver.resolve_answer_cached("example.com", "TXT")
    assert mock.call_count == 1


@pytest.mark.parametrize(
    "error",
    [ServfailError("example.com", "TXT"), ResolutionTimeoutError("example.com", "TXT")],
)
def test_failures_are_cached_briefly(error):
    """RFC 9520 3.2: a slow nameserver must not cost a timeout per message."""
    with (
        patch.object(dns_resolver, "_resolve", side_effect=error) as mock,
        patch.object(dns_resolver, "_cache_set", wraps=dns_resolver._cache_set) as put,
    ):
        for _ in range(2):
            with pytest.raises(type(error)):
                dns_resolver.resolve_answer_cached("example.com", "TXT")
    assert mock.call_count == 1
    assert put.call_args.args[2] == 30


@override_settings(DNS_RESOLVER_CACHE_FAILURE_TTL=0)
def test_failure_cache_can_be_disabled():
    error = ServfailError("example.com", "TXT")
    with patch.object(dns_resolver, "_resolve", side_effect=error) as mock:
        for _ in range(2):
            with pytest.raises(ServfailError):
                dns_resolver.resolve_answer_cached("example.com", "TXT")
    assert mock.call_count == 2


@override_settings(DNS_RESOLVER_MAX_RESOLUTION_TIME=10)
@pytest.mark.parametrize(("budget", "cached"), [(9.5, False), (10.5, True)])
def test_timeout_is_cached_only_with_a_full_budget(budget, cached):
    """Cut short by the budget, it says nothing about the nameserver.

    The lookup must get the same remaining budget the decision is made on:
    with the clock moving 1 s between two reads, they would disagree.
    """
    error = ResolutionTimeoutError("example.com", "TXT")
    clock = iter(range(1000, 2000))
    with (
        patch.object(dns_resolver.time, "monotonic", side_effect=lambda: next(clock)),
        patch.object(dns_resolver, "_resolve", side_effect=error) as mock,
    ):
        for _ in range(2):
            with dns_resolver.dns_budget(budget + 1), pytest.raises(error.__class__):
                dns_resolver.resolve_answer_cached("example.com", "TXT")
    assert mock.call_args_list[0].args[2] == budget
    assert mock.call_count == (1 if cached else 2)


def test_other_errors_are_not_cached():
    error = DNSSECError("bogus", "example.com", "TXT")
    with patch.object(dns_resolver, "_resolve", side_effect=error) as mock:
        for _ in range(2):
            with pytest.raises(DNSSECError):
                dns_resolver.resolve_answer_cached("example.com", "TXT")
    assert mock.call_count == 2


@override_settings(DNS_RESOLVER_CACHE_MAX_TTL=0)
def test_cache_can_be_disabled():
    with patch.object(dns_resolver, "_resolve") as mock_resolve:
        mock_resolve.return_value = txt_answer("v=spf1 -all")
        dns_resolver.resolve_answer_cached("example.com", "TXT")
        dns_resolver.resolve_answer_cached("example.com", "TXT")
    assert mock_resolve.call_count == 2


def test_cache_is_keyed_by_backend():
    """Switching backend must not serve answers the other one produced."""
    with patch.object(dns_resolver, "_resolve") as mock_resolve:
        mock_resolve.return_value = txt_answer("v=spf1 -all")
        dns_resolver.resolve_answer_cached("example.com", "TXT")
        with override_settings(DNS_RESOLVER_BACKEND="recursive"):
            dns_resolver.resolve_answer_cached("example.com", "TXT")
    assert mock_resolve.call_count == 2


@pytest.mark.parametrize(
    "entry", [("answer", ("x",)), ("answer", None), "garbage", ("bogus", 1)]
)
def test_unreadable_cache_entry_is_a_miss(entry):
    with patch.object(dns_resolver, "_resolve") as mock_resolve:
        mock_resolve.return_value = txt_answer("v=spf1 -all")
        cache.set(dns_resolver._cache_key("example.com", "TXT"), entry, 60)
        answer = dns_resolver.resolve_answer_cached("example.com", "TXT")
    assert answer.text_values() == ["v=spf1 -all"]
    mock_resolve.assert_called_once()


def test_cache_outage_degrades_to_uncached_lookup():
    with (
        patch.object(dns_resolver, "_resolve") as mock_resolve,
        patch.object(dns_resolver.cache, "get", side_effect=ConnectionError),
        patch.object(dns_resolver.cache, "set", side_effect=ConnectionError),
    ):
        mock_resolve.return_value = txt_answer("v=spf1 -all")
        answer = dns_resolver.resolve_answer_cached("example.com", "TXT")
    assert answer.text_values() == ["v=spf1 -all"]
