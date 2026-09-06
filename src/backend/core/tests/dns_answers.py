"""Builders for :class:`core.services.dns.resolver.Answer` fixtures.

Real rrsets rather than mocks, so tests exercise the same ``text_values()`` /
``records`` accessors production does — a mock with a ``.strings`` attribute
would happily return a shape the resolver never produces, which is how a TXT
record split across character-strings can pass its test and still fail in the
wild.
"""

import dns.name
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import dns.rdtypes.ANY.TXT
import dns.rrset

from core.services.dns.resolver import Answer, ValidationState

DEFAULT_TTL = 300


def answer_from_rdata(name, rdtype, rdatas, *, secure=False, ttl=DEFAULT_TTL):
    """Wrap already-built rdata objects in an Answer."""
    rdtype_int = dns.rdatatype.from_text(rdtype)
    qname = dns.name.from_text(name)
    rrset = dns.rrset.from_rdata_list(qname, ttl, rdatas)
    return Answer(
        qname=qname,
        canonical_name=qname,
        rdtype=rdtype_int,
        rrset=rrset,
        ttl=ttl,
        dnssec=ValidationState.SECURE if secure else ValidationState.INSECURE,
    )


def answer(name, rdtype, *values, secure=False, ttl=DEFAULT_TTL):
    """Answer built from presentation-format record values.

    e.g. ``answer("example.com", "MX", "10 mx1.example.com.")``.
    """
    rdatas = [
        dns.rdata.from_text(dns.rdataclass.IN, dns.rdatatype.from_text(rdtype), value)
        for value in values
    ]
    return answer_from_rdata(name, rdtype, rdatas, secure=secure, ttl=ttl)


def txt_answer(*values, name="example.com", secure=False, ttl=DEFAULT_TTL):
    """TXT answer with one record per value, each a single character-string.

    A value longer than 255 octets is split across chunks the way an
    authoritative server must, so callers do not have to think about the limit.
    """
    return txt_answer_chunked(
        *[_split_chunks(v) for v in values], name=name, secure=secure, ttl=ttl
    )


def txt_answer_chunked(*records, name="example.com", secure=False, ttl=DEFAULT_TTL):
    """TXT answer where each record is given as its list of character-strings.

    Use this to pin down how a multi-chunk record is reassembled; RFC 6376
    §3.6.2.2 and RFC 7208 §3.3 both require joining with no separator.
    """
    rdatas = [
        dns.rdata.from_text(
            dns.rdataclass.IN,
            dns.rdatatype.TXT,
            " ".join(f'"{_escape(chunk)}"' for chunk in chunks),
        )
        for chunks in records
    ]
    return answer_from_rdata(name, "TXT", rdatas, secure=secure, ttl=ttl)


def txt_answer_raw(*records, name="example.com", secure=False, ttl=DEFAULT_TTL):
    """TXT answer whose character-strings are given as raw bytes.

    A TXT record may hold any octets, which the presentation-format builders
    above cannot express: they go through ``from_text``, and that refuses
    anything not encodable as UTF-8.
    """
    rdatas = [
        dns.rdtypes.ANY.TXT.TXT(dns.rdataclass.IN, dns.rdatatype.TXT, list(chunks))
        for chunks in records
    ]
    return answer_from_rdata(name, "TXT", rdatas, secure=secure, ttl=ttl)


def mx_answer(*pairs, name="example.com", secure=False, ttl=DEFAULT_TTL):
    """MX answer from ``(preference, exchange)`` pairs."""
    return answer(
        name,
        "MX",
        *[f"{pref} {exchange}" for pref, exchange in pairs],
        secure=secure,
        ttl=ttl,
    )


def ns_answer(*targets, name="example.com", secure=False, ttl=DEFAULT_TTL):
    """NS answer from nameserver hostnames."""
    return answer(name, "NS", *targets, secure=secure, ttl=ttl)


def a_answer(*addresses, name="example.com", secure=False, ttl=DEFAULT_TTL):
    """A answer from IPv4 addresses."""
    return answer(name, "A", *addresses, secure=secure, ttl=ttl)


def _escape(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _split_chunks(value):
    encoded = value.encode()
    if len(encoded) <= 255:
        return [value]
    return [
        encoded[i : i + 255].decode("utf-8", "surrogateescape")
        for i in range(0, len(encoded), 255)
    ]
