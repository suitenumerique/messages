"""Unit tests for ``core.mda.spf`` beyond the RFC 7208 conformance suite."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.mda import spf
from core.services.dns.resolver import (
    DNSSECValidationError,
    InvalidNameError,
    NoAnswerError,
    NXDOMAINError,
    ResolutionTimeoutError,
    ServfailError,
    UnsupportedRdtypeError,
)
from core.tests.dns_answers import answer, txt_answer, txt_answer_chunked


class FakeLookup(spf.SPFLookup):
    """Answers from a {(name, type): values} map; anything else is void."""

    def __init__(self, records):
        super().__init__()
        self.records = records
        self.queries = []

    def _get(self, name, rtype):
        self.queries.append((name, rtype))
        value = self.records.get((name.lower(), rtype), [])
        if isinstance(value, Exception):
            raise value
        return value

    def txt(self, name):
        key = (name.lower(), "TXT")
        if key not in self.records and not any(n == key[0] for n, _ in self.records):
            self.queries.append((name, "TXT"))
            return [], True
        return self._get(name, "TXT"), False

    def addresses(self, name, version):
        return self._get(name, "A" if version == 4 else "AAAA")

    def mx_hosts(self, name):
        return self._get(name, "MX")

    def ptr(self, ip):
        return self._get(ip, "PTR")


# --- SPFLookup over the resolver -------------------------------------------


def _resolver(answers):
    def _resolve(name, rdtype):
        value = answers.get((name, rdtype), NXDOMAINError(name, rdtype))
        if isinstance(value, Exception):
            raise value
        return value

    return _resolve


def test_lookup_joins_txt_chunks():
    lookup = spf.SPFLookup(
        _resolver(
            {
                ("example.com", "TXT"): txt_answer_chunked(
                    ["v=spf1 ip4:192.0.2.0/24", " -all"]
                )
            }
        )
    )
    assert lookup.txt("example.com") == (["v=spf1 ip4:192.0.2.0/24 -all"], False)


def test_lookup_reports_nxdomain_apart_from_nodata():
    lookup = spf.SPFLookup(
        _resolver({("nodata.example.com", "TXT"): NoAnswerError("x", "TXT")})
    )
    assert lookup.txt("missing.example.com") == ([], True)
    assert lookup.txt("nodata.example.com") == ([], False)


@pytest.mark.parametrize(
    "error",
    [ServfailError("example.com", "TXT"), ResolutionTimeoutError("example.com", "TXT")],
)
def test_lookup_incomplete_is_temperror(error):
    lookup = spf.SPFLookup(_resolver({("example.com", "TXT"): error}))
    with pytest.raises(spf.SPFTempError):
        lookup.txt("example.com")


@pytest.mark.parametrize(
    "error",
    [InvalidNameError("x" * 300, "too long"), NoAnswerError("example.com", "A")],
)
def test_lookup_settled_errors_are_void(error):
    lookup = spf.SPFLookup(_resolver({("example.com", "A"): error}))
    assert lookup.addresses("example.com", 4) == []


def test_lookup_bogus_dnssec_is_temperror():
    """Bogus records must not read as "none", or ``-all`` would fail them."""
    lookup = spf.SPFLookup(
        _resolver(
            {("example.com", "A"): DNSSECValidationError("example.com", "A", "bogus")}
        )
    )
    with pytest.raises(spf.SPFTempError):
        lookup.addresses("example.com", 4)


def test_lookup_unsupported_type_is_permerror():
    lookup = spf.SPFLookup(
        _resolver({("example.com", "A"): UnsupportedRdtypeError("A")})
    )
    with pytest.raises(spf.SPFPermError):
        lookup.addresses("example.com", 4)


def test_lookup_reads_addresses_mx_and_ptr():
    lookup = spf.SPFLookup(
        _resolver(
            {
                ("example.com", "AAAA"): answer("example.com", "AAAA", "2001:db8::1"),
                ("example.com", "MX"): answer(
                    "example.com", "MX", "10 mx1.example.com.", "20 mx2.example.com."
                ),
                ("192.0.2.1", "PTR"): answer(
                    "1.2.0.192.in-addr.arpa", "PTR", "mail.example.com."
                ),
            }
        )
    )
    assert lookup.addresses("example.com", 6) == ["2001:db8::1"]
    assert sorted(lookup.mx_hosts("example.com")) == [
        "mx1.example.com",
        "mx2.example.com",
    ]
    assert lookup.ptr("192.0.2.1") == ["mail.example.com"]


def test_default_lookup_uses_the_shared_cache():
    """Third-party lookups at inbound volume go through the Redis cache."""
    with patch(
        "core.mda.spf.resolve_answer_cached",
        return_value=txt_answer("v=spf1 -all"),
    ) as mock_cached:
        assert spf.SPFLookup().txt("example.com") == (["v=spf1 -all"], False)
    mock_cached.assert_called_once_with("example.com", "TXT")


# --- evaluate_spf identities -----------------------------------------------


def test_mail_from_identity():
    lookup = FakeLookup({("example.com", "TXT"): ["v=spf1 ip4:192.0.2.1 -all"]})
    result = spf.evaluate_spf("192.0.2.1", "bounce@Example.COM", "mx.test", lookup)
    assert result == spf.SPFResult("pass", "example.com", "mailfrom", "")


def test_unicode_mail_from_domain_is_queried_and_reported_as_a_label():
    """Alignment compares against the From domain's A-label."""
    lookup = FakeLookup(
        {("xn--bcher-kva.example", "TXT"): ["v=spf1 ip4:192.0.2.1 -all"]}
    )
    result = spf.evaluate_spf("192.0.2.1", "a@bücher.example", "mx.test", lookup)
    assert result.result == "pass"
    assert result.domain == "xn--bcher-kva.example"


@pytest.mark.parametrize("mail_from", ["", "<>", "  "])
def test_null_sender_falls_back_to_helo(mail_from):
    """RFC 7208 2.4: a bounce is checked against the HELO identity."""
    lookup = FakeLookup({("mx.example.com", "TXT"): ["v=spf1 a -all"]})
    lookup.records[("mx.example.com", "A")] = ["192.0.2.1"]
    result = spf.evaluate_spf("192.0.2.1", mail_from, "mx.example.com.", lookup)
    assert result.result == "pass"
    assert result.domain == "mx.example.com"
    assert result.identity == "helo"


def test_null_sender_without_helo_is_none():
    result = spf.evaluate_spf("192.0.2.1", "", "", FakeLookup({}))
    assert result.result == "none"
    assert result.domain is None


def test_mail_from_without_domain_is_none():
    result = spf.evaluate_spf("192.0.2.1", "nodomain", "mx.test", FakeLookup({}))
    assert result.result == "none"


def test_invalid_client_ip_is_none():
    result = spf.evaluate_spf("not-an-ip", "a@example.com", "mx", FakeLookup({}))
    assert result.result == "none"


def test_ipv4_mapped_client_is_treated_as_ipv4():
    """An IPv6 listener reports IPv4 peers as ::ffff:a.b.c.d (RFC 7208 5)."""
    lookup = FakeLookup({("example.com", "TXT"): ["v=spf1 ip4:192.0.2.1 -all"]})
    result = spf.evaluate_spf("::ffff:192.0.2.1", "a@example.com", "mx", lookup)
    assert result.result == "pass"


def test_ip6_mechanism():
    lookup = FakeLookup({("example.com", "TXT"): ["v=spf1 ip6:2001:db8::/32 -all"]})
    assert spf.evaluate_spf("2001:db8::5", "a@example.com", "mx", lookup).result == (
        "pass"
    )
    assert spf.evaluate_spf("2001:db9::5", "a@example.com", "mx", lookup).result == (
        "fail"
    )


@pytest.mark.parametrize(
    "record",
    [
        "v=spf1 ip4:192.0.2.1/" + "1" * 5000 + " -all",
        "v=spf1 a/" + "1" * 5000 + " -all",
        "v=spf1 exists:%{l" + "1" * 5000 + "}.example.com -all",
    ],
)
def test_huge_digit_strings_are_permerror_not_crash(record):
    """int() raises past 4300 digits: must stay a plain permerror."""
    lookup = FakeLookup({("example.com", "TXT"): [record]})
    result, _ = spf.check_host(
        "192.0.2.1", "example.com", "a@example.com", lookup=lookup
    )
    assert result == "permerror"


# --- limits ----------------------------------------------------------------


def test_deadline_is_temperror():
    lookup = FakeLookup({("example.com", "TXT"): ["v=spf1 a a a -all"]})
    result, reason = spf.check_host(
        "192.0.2.1", "example.com", "a@example.com", lookup=lookup, timeout=-1
    )
    assert result == "temperror"
    assert "time limit" in reason


def test_deadline_during_ptr_validation_is_temperror():
    """Stopping validation early must not let "-all" return a definite fail."""
    clock = [0.0]

    class SlowPtrLookup(FakeLookup):
        def ptr(self, ip):
            clock[0] = 1000.0
            return super().ptr(ip)

    lookup = SlowPtrLookup(
        {
            ("example.com", "TXT"): ["v=spf1 ptr -all"],
            ("192.0.2.1", "PTR"): ["host.example.com"],
            ("host.example.com", "A"): ["192.0.2.1"],
        }
    )
    with patch.object(spf, "time", SimpleNamespace(monotonic=lambda: clock[0])):
        result, _ = spf.check_host(
            "192.0.2.1", "example.com", "a@example.com", lookup=lookup
        )
    assert result == "temperror"


def test_empty_mx_address_answer_counts_as_void():
    """4.6.4: the third void lookup is a permerror, MX exchanges included."""
    lookup = FakeLookup(
        {
            ("example.com", "TXT"): [
                "v=spf1 a:v1.example.com a:v2.example.com mx:m.example.com -all"
            ],
            ("m.example.com", "MX"): ["mx1.example.com"],
        }
    )
    result, _ = spf.check_host(
        "192.0.2.1", "example.com", "a@example.com", lookup=lookup
    )
    assert result == "permerror"


def test_include_loop_hits_the_lookup_limit():
    lookup = FakeLookup({("example.com", "TXT"): ["v=spf1 include:example.com -all"]})
    result, _ = spf.check_host(
        "192.0.2.1", "example.com", "a@example.com", lookup=lookup
    )
    assert result == "permerror"
    assert len(lookup.queries) == spf.MAX_DNS_TERMS + 1


def test_more_than_ten_lookups_are_allowed():
    """Past the RFC's 10, up to MAX_DNS_TERMS (30) still evaluates."""
    records = {("example.com", "TXT"): ["v=spf1 include:i0.example.com -all"]}
    for i in range(20):
        records[(f"i{i}.example.com", "TXT")] = [f"v=spf1 include:i{i + 1}.example.com"]
    records[("i20.example.com", "TXT")] = ["v=spf1 ip4:192.0.2.1"]
    lookup = FakeLookup(records)
    result, _ = spf.check_host(
        "192.0.2.1", "example.com", "a@example.com", lookup=lookup
    )
    assert result == "pass"


def test_ptr_keeps_only_the_first_ten_names():
    names = [f"h{i}.example.com" for i in range(15)]
    records = {
        ("example.com", "TXT"): ["v=spf1 ptr -all"],
        ("192.0.2.1", "PTR"): names,
    }
    for name in names:
        records[(name, "A")] = ["192.0.2.1"]
    lookup = FakeLookup(records)
    assert spf.check_host("192.0.2.1", "example.com", "a@example.com", lookup=lookup)[
        0
    ] == ("pass")
    address_queries = [q for q in lookup.queries if q[1] == "A"]
    assert len(address_queries) == spf.MAX_PTR_RECORDS


def test_ptr_dns_errors_do_not_error():
    """5.5: a failed PTR lookup only fails to match."""
    lookup = FakeLookup(
        {
            ("example.com", "TXT"): ["v=spf1 ptr -all"],
            ("192.0.2.1", "PTR"): spf.SPFTempError("down"),
        }
    )
    assert spf.check_host("192.0.2.1", "example.com", "a@example.com", lookup=lookup)[
        0
    ] == ("fail")


def test_p_macro_uses_a_validated_name():
    lookup = FakeLookup(
        {
            ("example.com", "TXT"): ["v=spf1 exists:%{p}.allow.example.com -all"],
            ("192.0.2.1", "PTR"): ["forged.example.org", "mail.example.com"],
            ("mail.example.com", "A"): ["192.0.2.1"],
            ("forged.example.org", "A"): ["198.51.100.1"],
            ("mail.example.com.allow.example.com", "A"): ["127.0.0.2"],
        }
    )
    assert spf.check_host("192.0.2.1", "example.com", "a@example.com", lookup=lookup)[
        0
    ] == ("pass")


def test_uppercase_macro_is_url_escaped():
    lookup = FakeLookup(
        {
            ("example.com", "TXT"): ["v=spf1 exists:%{L}.x.example.com -all"],
            # FakeLookup folds names, so the escape is keyed in lowercase.
            ("a%2bb.x.example.com", "A"): ["127.0.0.2"],
        }
    )
    assert spf.check_host("192.0.2.1", "example.com", "a+b@example.com", lookup=lookup)[
        0
    ] == ("pass")
