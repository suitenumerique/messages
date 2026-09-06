"""Tests for the shared recursive resolver.

Every other DNS test patches its call site, so nothing else exercises this
module: a wrong settings name or constructor keyword here would only surface
in production, where the failure is "no DNS at all".
"""

from unittest.mock import patch

from django.test import override_settings

from core.services.dns import resolver as dns_resolver
from core.services.dns.resolver import RecursiveResolver
from core.tests.dns_answers import txt_answer


def _fresh_resolver():
    """Drop the process-wide instance so settings are read again."""
    dns_resolver.get_resolver.cache_clear()


def test_get_resolver_builds_from_settings():
    """The settings names and constructor keywords line up."""
    _fresh_resolver()
    try:
        with override_settings(
            DNS_RESOLVER_TIMEOUT=1.5, DNS_RESOLVER_MAX_RESOLUTION_TIME=7.5
        ):
            built = dns_resolver.get_resolver()
            assert isinstance(built, RecursiveResolver)
            assert built.timeout == 1.5
            assert built.max_resolution_time == 7.5
    finally:
        _fresh_resolver()


def test_get_resolver_is_shared():
    """One instance per process, so the delegation cache is actually reused."""
    _fresh_resolver()
    try:
        assert dns_resolver.get_resolver() is dns_resolver.get_resolver()
    finally:
        _fresh_resolver()


def test_record_data_is_never_cached():
    """A customer's published record must take effect on the next lookup.

    Both halves are needed: ``cache_answers`` does not gate NXDOMAIN/NODATA,
    so without the negative TTL a freshly created record keeps reading as
    missing for ``negative_ttl`` seconds — the exact complaint.
    """
    _fresh_resolver()
    try:
        built = dns_resolver.get_resolver()
        assert built.cache_answers is False
        assert built.cache.max_negative_ttl == 0
    finally:
        _fresh_resolver()


def test_only_the_roots_delegations_are_cached():
    """Root referrals kept, every cut below re-resolved.

    Keeping the root's delegations is what stops us re-querying root servers,
    a shared resource, for data that never moves. Dropping everything below is
    what makes a domain that changed nameservers — or was registered a minute
    ago — resolve against its new servers now, rather than out of an NS set
    cached for as long as its TTL asks.

    Depth is counted root=0, ``com.``=1, ``example.com.``=2, so 1 is exactly
    "the root's referrals and nothing further".
    """
    _fresh_resolver()
    try:
        assert dns_resolver.get_resolver().cache.max_delegation_depth == 1
    finally:
        _fresh_resolver()


def test_resolve_txt_values_joins_character_strings():
    """One value per record, chunks joined — what DKIM and SPF both require."""
    answer = txt_answer("v=spf1 -all", "x" * 300)
    with patch.object(dns_resolver, "get_resolver") as mock_get:
        mock_get.return_value.resolve_answer.return_value = answer

        values = dns_resolver.resolve_txt_values("example.com")

    # The 300-character value was split across two character-strings by the
    # builder, exactly as an authoritative server must, and comes back whole.
    assert values == ["v=spf1 -all", "x" * 300]
    mock_get.return_value.resolve_answer.assert_called_once_with("example.com", "TXT")


def test_resolve_answer_defaults_to_a_records():
    """``rdtype`` defaults to A, matching the hostname lookups that omit it."""
    with patch.object(dns_resolver, "get_resolver") as mock_get:
        dns_resolver.resolve_answer("mx1.example.com")

    mock_get.return_value.resolve_answer.assert_called_once_with("mx1.example.com", "A")
