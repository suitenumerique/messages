"""SPF evaluation: RFC 7208 ``check_host()``.

Used by native inbound authentication to feed DMARC (RFC 7489 4.2), so only
the result and the domain it was evaluated for matter. The ``exp`` modifier
is parsed for syntax but never fetched: an explanation string is for an SMTP
rejection, and we evaluate after the message was accepted.

Every lookup goes through :class:`SPFLookup`, which wraps the shared resolver
(``core.services.dns.resolver``) behind its application-level cache. Tests
substitute their own.

Limits (4.6.4) are the anti-amplification caps and are enforced as
``permerror``: 30 DNS-querying terms across the whole evaluation (see
``MAX_DNS_TERMS``), 2 void lookups, 10 address lookups per ``mx``. ``ptr`` silently keeps its first 10
names. An overall deadline turns a slow chain into ``temperror``.
"""

import dataclasses
import ipaddress
import logging
import re
import time
import urllib.parse
from typing import Callable, Optional

from core.mda.addresses import (
    address_domain,
    address_local_part,
    ascii_lower,
    normalize_domain,
)
from core.services.dns.records import is_spf_record
from core.services.dns.resolver import (
    DNSSECError,
    InvalidNameError,
    NoAnswerError,
    NXDOMAINError,
    ResolverError,
    UnsupportedRdtypeError,
    resolve_answer_cached,
)

logger = logging.getLogger(__name__)

# RFC 7208 2.6 results.
NONE = "none"
NEUTRAL = "neutral"
PASS = "pass"  # noqa: S105 (SPF result token, not a password)
FAIL = "fail"
SOFTFAIL = "softfail"
TEMPERROR = "temperror"
PERMERROR = "permerror"

_QUALIFIER_RESULTS = {"+": PASS, "-": FAIL, "~": SOFTFAIL, "?": NEUTRAL}

# 4.6.4 says 10. rspamd, which native mode replaces, allows 30
# (SPF_MAX_DNS_REQUESTS). At 10, a domain whose record goes over 10 and that
# rspamd passes would get permerror, a DMARC fail, so its mail without an
# aligned DKIM signature would show as forged. The DNS budget bounds the cost.
MAX_DNS_TERMS = 30
MAX_VOID_LOOKUPS = 2
MAX_MX_RECORDS = 10
MAX_PTR_RECORDS = 10
# 4.6.4 asks for at least 20 seconds if a limit is imposed at all.
DEFAULT_TIMEOUT = 20.0
# 4.3 / 7.3: a name used in a query is at most 253 characters.
_MAX_DOMAIN_LENGTH = 253

_MECHANISMS = frozenset({"all", "include", "a", "mx", "ptr", "ip4", "ip6", "exists"})
_MODIFIER_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*")
# A term's name ends at the first ":", "=" or "/" (4.6.1).
_TERM_NAME_RE = re.compile(r"[^:=/]*")
# 12: macro-literal is %x21-24 / %x26-7E, i.e. visible ASCII except "%".
_MACRO_LITERAL_RE = re.compile(r"[\x21-\x24\x26-\x7e]")
_MACRO_EXPAND_RE = re.compile(
    # Up to 3 digits: a label count beyond that is meaningless, and int()
    # refuses strings over 4300 digits.
    r"%\{(?P<letter>[a-zA-Z])(?P<digits>[0-9]{0,3})(?P<reverse>[rR]?)"
    r"(?P<delimiters>[.\-+,/_=]*)\}"
)
# 7.1: "%%" is "%", "%_" a space, "%-" an URL-encoded space.
_MACRO_ESCAPES = {"%%": "%", "%_": " ", "%-": "%20"}
# 12: domain-end = ( "." toplabel [ "." ] ) / macro-expand
_DOMAIN_END_MACRO_RE = re.compile(r"(?:" + _MACRO_EXPAND_RE.pattern + r"|%[%_-])$")
_DOMAIN_MACRO_LETTERS = frozenset("slodiphv")
_EXP_ONLY_MACRO_LETTERS = frozenset("crt")
# 12: toplabel = ( *alphanum ALPHA *alphanum ) /
#                ( 1*alphanum "-" *( alphanum / "-" ) alphanum )
_TOPLABEL_RE = re.compile(
    r"(?:[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*|[A-Za-z0-9]+-[A-Za-z0-9-]*[A-Za-z0-9])"
)
_DUAL_CIDR_RE = re.compile(r"^(?P<spec>.*?)(?:/(?P<v4>[0-9]+))?(?://(?P<v6>[0-9]+))?$")
_CIDR_RE = re.compile(r"^(?P<net>.*?)(?:/(?P<len>[0-9]+))?$")


class SPFPermError(Exception):
    """The record cannot be evaluated: ``permerror``."""


class SPFTempError(Exception):
    """A lookup did not complete: ``temperror``."""


@dataclasses.dataclass(frozen=True)
class SPFResult:
    """Outcome of an SPF evaluation.

    ``domain`` is the identity that was checked (MAIL FROM domain, or the
    HELO name for a null sender), which is what DMARC aligns against.
    """

    result: str
    domain: Optional[str]
    identity: str
    reason: str = ""


class SPFLookup:
    """DNS access for one evaluation.

    Each method returns the record values, an empty list for a void answer
    (NXDOMAIN or NODATA), and raises :class:`SPFTempError` for a lookup that
    did not complete. ``nxdomain`` tells NXDOMAIN apart, which 4.3 needs for
    the initial TXT query.
    """

    def __init__(self, resolve: Optional[Callable] = None):
        self._resolve = resolve

    def _query(self, name: str, rdtype: str):
        resolve = self._resolve or resolve_answer_cached
        try:
            return resolve(name, rdtype), False
        except NXDOMAINError:
            return None, True
        except (NoAnswerError, InvalidNameError):
            return None, False
        except DNSSECError as exc:
            # Bogus data (recursive backend) is not the publisher's, and
            # reading it as "no records" could let ``-all`` mark legitimate
            # mail forged. Unknown, like a validating stub's SERVFAIL.
            raise SPFTempError(f"{name}/{rdtype}: DNSSEC: {exc}") from exc
        except UnsupportedRdtypeError as exc:
            raise SPFPermError(str(exc)) from exc
        except ResolverError as exc:
            raise SPFTempError(f"{name}/{rdtype}: {exc}") from exc

    def txt(self, name: str) -> tuple[list[str], bool]:
        """TXT values (chunks joined), and whether the name is NXDOMAIN."""
        answer, nxdomain = self._query(name, "TXT")
        if answer is None:
            return [], nxdomain
        values = []
        for rdata in answer.rrset:
            raw = b"".join(rdata.strings)
            # Non-ASCII is kept undecodable-as-ASCII on purpose, so the
            # record check below can reject it (3.1).
            values.append(raw.decode("utf-8", "surrogateescape"))
        return values, False

    def addresses(self, name: str, version: int) -> list[str]:
        """A (IPv4) or AAAA (IPv6) addresses of ``name``."""
        answer, _ = self._query(name, "A" if version == 4 else "AAAA")
        return [] if answer is None else [rdata.address for rdata in answer.rrset]

    def mx_hosts(self, name: str) -> list[str]:
        """MX exchange names of ``name``."""
        answer, _ = self._query(name, "MX")
        if answer is None:
            return []
        return [rdata.exchange.to_text(omit_final_dot=True) for rdata in answer.rrset]

    def ptr(self, ip: str) -> list[str]:
        """PTR names of the address ``ip``."""
        answer, _ = self._query(ip, "PTR")
        if answer is None:
            return []
        return [rdata.target.to_text(omit_final_dot=True) for rdata in answer.rrset]


@dataclasses.dataclass
class _Term:
    qualifier: str
    name: str
    domain_spec: Optional[str] = None
    network: Optional[ipaddress.IPv4Network | ipaddress.IPv6Network] = None
    cidr4: int = 32
    cidr6: int = 128


@dataclasses.dataclass
class _Record:
    directives: list
    redirect: Optional[str]
    has_all: bool


def _check_macro_string(value: str, *, allow_exp_letters: bool = False) -> None:
    """Validate a macro-string (7.1), raising :class:`SPFPermError`."""
    position = 0
    while position < len(value):
        char = value[position]
        if char != "%":
            if not _MACRO_LITERAL_RE.fullmatch(char):
                raise SPFPermError(f"invalid character {char!r} in {value!r}")
            position += 1
            continue
        following = value[position + 1 : position + 2]
        if following in ("%", "_", "-"):
            position += 2
            continue
        match = _MACRO_EXPAND_RE.match(value, position)
        if not match:
            raise SPFPermError(f"invalid macro in {value!r}")
        letter = match.group("letter").lower()
        allowed = _DOMAIN_MACRO_LETTERS | (
            _EXP_ONLY_MACRO_LETTERS if allow_exp_letters else frozenset()
        )
        if letter not in allowed:
            raise SPFPermError(f"invalid macro letter {letter!r} in {value!r}")
        digits = match.group("digits")
        if digits and int(digits) == 0:
            raise SPFPermError(f"zero digit transformer in {value!r}")
        position = match.end()


def _check_domain_spec(spec: str) -> None:
    """Validate a domain-spec: macro-string + domain-end (12)."""
    if not spec:
        raise SPFPermError("empty domain-spec")
    _check_macro_string(spec)
    if _DOMAIN_END_MACRO_RE.search(spec):
        return
    labels = spec[:-1] if spec.endswith(".") else spec
    if "." not in labels:
        raise SPFPermError(f"domain-spec {spec!r} has no top label")
    toplabel = labels.rsplit(".", 1)[1]
    if not _TOPLABEL_RE.fullmatch(toplabel):
        raise SPFPermError(f"invalid top label in {spec!r}")


def _cidr(length: Optional[str], maximum: int) -> int:
    if length is None:
        return maximum
    # Length first: int() refuses strings over 4300 digits with a ValueError.
    if (
        len(length) > 3
        or (length.startswith("0") and length != "0")
        or int(length) > maximum
    ):
        raise SPFPermError(f"invalid CIDR length /{length}")
    return int(length)


def _parse_directive(qualifier: str, name: str, argument: str) -> _Term:
    term = _Term(qualifier=qualifier or "+", name=name)
    if name == "all":
        if argument:
            raise SPFPermError(f"'all' takes no argument: {argument!r}")
        return term

    if name in ("ip4", "ip6"):
        if not argument.startswith(":"):
            raise SPFPermError(f"{name} needs an address")
        match = _CIDR_RE.match(argument[1:])
        maximum = 32 if name == "ip4" else 128
        length = _cidr(match.group("len"), maximum)
        if "%" in match.group("net"):
            # ``ipaddress`` accepts an IPv6 scope ("fe80::1%eth0"); 12 does not.
            raise SPFPermError(f"invalid {name} address {argument!r}")
        try:
            address = ipaddress.ip_address(match.group("net"))
        except ValueError as exc:
            raise SPFPermError(f"invalid {name} address {argument!r}") from exc
        if address.version != (4 if name == "ip4" else 6):
            raise SPFPermError(f"{name} with a non-{name} address {argument!r}")
        term.network = ipaddress.ip_network(f"{address}/{length}", strict=False)
        return term

    if name in ("include", "exists"):
        if not argument.startswith(":"):
            raise SPFPermError(f"{name} needs a domain-spec")
        term.domain_spec = argument[1:]
        _check_domain_spec(term.domain_spec)
        return term

    if name == "ptr":
        if argument:
            if not argument.startswith(":"):
                raise SPFPermError(f"invalid ptr argument {argument!r}")
            term.domain_spec = argument[1:]
            _check_domain_spec(term.domain_spec)
        return term

    # "a" and "mx": [ ":" domain-spec ] [ dual-cidr-length ]
    if argument and argument[0] not in ":/":
        raise SPFPermError(f"invalid {name} argument {argument!r}")
    match = _DUAL_CIDR_RE.match(argument)
    spec = match.group("spec")
    term.cidr4 = _cidr(match.group("v4"), 32)
    term.cidr6 = _cidr(match.group("v6"), 128)
    if spec:
        if not spec.startswith(":"):
            raise SPFPermError(f"invalid {name} argument {argument!r}")
        term.domain_spec = spec[1:]
        _check_domain_spec(term.domain_spec)
    return term


def _parse_record(record: str) -> _Record:
    """Parse a whole record up front: any syntax error is a permerror (4.6)."""
    directives = []
    redirect = None
    exp_seen = False
    has_all = False
    # Terms are separated by SP only (4.6.1); "v=spf1" is 6 characters.
    for raw_term in record[6:].split(" "):
        if not raw_term:
            continue
        qualifier, term = "", raw_term
        if term[0] in "+-?~":
            qualifier, term = term[0], term[1:]
        name = _TERM_NAME_RE.match(term).group()
        argument = term[len(name) :]
        if argument.startswith("=") and not qualifier:
            if not _MODIFIER_NAME_RE.fullmatch(name):
                raise SPFPermError(f"invalid modifier name {name!r}")
            value = argument[1:]
            lowered = name.lower()
            if lowered == "redirect":
                if redirect is not None:
                    raise SPFPermError("more than one redirect modifier")
                _check_domain_spec(value)
                redirect = value
            elif lowered == "exp":
                if exp_seen:
                    raise SPFPermError("more than one exp modifier")
                _check_domain_spec(value)
                exp_seen = True
            else:
                _check_macro_string(value, allow_exp_letters=True)
            continue
        lowered = name.lower()
        if lowered not in _MECHANISMS:
            raise SPFPermError(f"unknown mechanism {term!r}")
        directive = _parse_directive(qualifier, lowered, argument)
        has_all = has_all or lowered == "all"
        directives.append(directive)
    return _Record(directives=directives, redirect=redirect, has_all=has_all)


def _ip_nibbles(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """The ``i`` macro: dotted quad for IPv4, dot-separated nibbles for IPv6."""
    if ip.version == 4:
        return str(ip)
    return ".".join(ip.exploded.replace(":", ""))


class _Evaluation:  # pylint: disable=too-many-instance-attributes
    """State shared across one ``check_host()`` and its nested calls."""

    def __init__(self, ip, sender: str, helo: str, lookup: SPFLookup, deadline):
        self.ip = ip
        self.sender = sender
        self.helo = helo
        self.lookup = lookup
        self.deadline = deadline
        self.dns_terms = 0
        self.void_lookups = 0
        self._validated_ptr: dict[str, str] = {}

    # --- limits ------------------------------------------------------------

    def _check_deadline(self):
        if time.monotonic() > self.deadline:
            raise SPFTempError("evaluation time limit exceeded")

    def count_term(self):
        """Count one DNS-querying term (at most ``MAX_DNS_TERMS``)."""
        self.dns_terms += 1
        if self.dns_terms > MAX_DNS_TERMS:
            raise SPFPermError("too many DNS lookups")
        self._check_deadline()

    def note_void(self, records) -> None:
        """Count an empty answer (4.6.4: at most 2 void lookups)."""
        if not records:
            self.void_lookups += 1
            if self.void_lookups > MAX_VOID_LOOKUPS:
                raise SPFPermError("too many void lookups")

    # --- macros ------------------------------------------------------------

    def expand(self, spec: str, domain: str) -> str:
        """Expand the macros of a domain-spec (7), for target ``domain``."""
        local = address_local_part(self.sender)
        sender_domain = address_domain(self.sender)

        def letter_value(letter: str) -> str:
            if letter == "s":
                return self.sender
            if letter == "l":
                return local
            if letter == "o":
                return sender_domain
            if letter == "d":
                return domain
            if letter == "i":
                return _ip_nibbles(self.ip)
            if letter == "p":
                return self.validated_ptr(domain)
            if letter == "v":
                return "in-addr" if self.ip.version == 4 else "ip6"
            if letter == "h":
                return self.helo
            raise SPFPermError(f"macro letter {letter!r} not allowed here")

        def replace(match: re.Match) -> str:
            if match.group(0) in _MACRO_ESCAPES:
                return _MACRO_ESCAPES[match.group(0)]
            raw_letter = match.group("letter")
            value = letter_value(raw_letter.lower())
            delimiters = match.group("delimiters") or "."
            parts = re.split("[" + re.escape(delimiters) + "]", value)
            if match.group("reverse"):
                parts.reverse()
            digits = match.group("digits")
            if digits:
                parts = parts[-int(digits) :]
            expanded = ".".join(parts)
            if raw_letter.isupper():
                expanded = urllib.parse.quote(expanded, safe="")
            return expanded

        expanded = re.sub(r"%%|%_|%-|" + _MACRO_EXPAND_RE.pattern, replace, spec)
        expanded = expanded.rstrip(".")
        # 7.3: truncate from the left, a whole label at a time.
        while len(expanded) > _MAX_DOMAIN_LENGTH and "." in expanded:
            expanded = expanded.split(".", 1)[1]
        return expanded

    def validated_ptr(self, domain: str) -> str:
        """The ``p`` macro (7.3): a validated name of the client, or "unknown"."""
        # Keyed by domain: which validated name is preferred depends on it.
        if domain not in self._validated_ptr:
            names = self._validated_names(domain)
            self._validated_ptr[domain] = names[0] if names else "unknown"
        return self._validated_ptr[domain]

    def _validated_names(self, domain: str) -> list[str]:
        """PTR names of the client that resolve back to it (5.5).

        DNS errors are not errors here: a failed PTR lookup matches nothing,
        and a failed address lookup skips that name.
        """
        try:
            names = self.lookup.ptr(str(self.ip))
        except SPFTempError:
            return []
        validated = []
        for name in names[:MAX_PTR_RECORDS]:
            # Raises: stopping early would let a later "-all" decide.
            self._check_deadline()
            try:
                addresses = self.lookup.addresses(name, self.ip.version)
            except SPFTempError:
                continue
            if any(ipaddress.ip_address(a) == self.ip for a in addresses):
                validated.append(name)
        # Prefer an exact match, then a subdomain of the target (5.5, 7.3).
        domain = ascii_lower(domain).rstrip(".")
        validated.sort(
            key=lambda n: (
                ascii_lower(n) != domain,
                not ascii_lower(n).endswith("." + domain),
            )
        )
        return validated

    # --- mechanisms --------------------------------------------------------

    def _in_network(self, addresses, cidr4: int, cidr6: int) -> bool:
        length = cidr4 if self.ip.version == 4 else cidr6
        for address in addresses:
            network = ipaddress.ip_network(f"{address}/{length}", strict=False)
            if self.ip in network:
                return True
        return False

    def matches(self, term: _Term, domain: str) -> bool:
        """Whether a directive's mechanism matches the client (5)."""
        if term.name == "all":
            return True
        if term.name in ("ip4", "ip6"):
            return self.ip.version == term.network.version and self.ip in term.network

        self.count_term()
        target = self.expand(term.domain_spec, domain) if term.domain_spec else domain

        if term.name == "include":
            result = self.check_host(target)
            if result == PASS:
                return True
            if result in (FAIL, SOFTFAIL, NEUTRAL):
                return False
            if result == TEMPERROR:
                raise SPFTempError(f"include:{target} temperror")
            raise SPFPermError(f"include:{target} returned {result}")

        if term.name == "a":
            addresses = self.lookup.addresses(target, self.ip.version)
            self.note_void(addresses)
            return self._in_network(addresses, term.cidr4, term.cidr6)

        if term.name == "mx":
            exchanges = self.lookup.mx_hosts(target)
            self.note_void(exchanges)
            exchanges = [e for e in exchanges if e and e != "."]
            if len(exchanges) > MAX_MX_RECORDS:
                raise SPFPermError(f"mx:{target} has more than {MAX_MX_RECORDS} MX")
            for exchange in exchanges:
                self._check_deadline()
                addresses = self.lookup.addresses(exchange, self.ip.version)
                self.note_void(addresses)
                if self._in_network(addresses, term.cidr4, term.cidr6):
                    return True
            return False

        if term.name == "ptr":
            target = target.lower().rstrip(".")
            for name in self._validated_names(target):
                lowered = name.lower().rstrip(".")
                if lowered == target or lowered.endswith("." + target):
                    return True
            return False

        if term.name == "exists":
            # Always an A query, whatever the client's address family (5.7).
            addresses = self.lookup.addresses(target, 4)
            self.note_void(addresses)
            return bool(addresses)

        raise SPFPermError(f"unknown mechanism {term.name}")

    # --- check_host --------------------------------------------------------

    def check_host(self, domain: str) -> str:
        """RFC 7208 4: evaluate the policy of ``domain``."""
        self._check_deadline()
        if not _domain_is_valid(domain):
            return NONE
        values, nxdomain = self.lookup.txt(domain)
        if nxdomain:
            return NONE
        records = [v for v in values if is_spf_record(v)]
        if not records:
            return NONE
        if len(records) > 1:
            raise SPFPermError(f"{domain} publishes {len(records)} SPF records")
        record = records[0]
        if not record.isascii() or not record.isprintable():
            raise SPFPermError(f"{domain} SPF record has invalid characters")

        parsed = _parse_record(record)
        for term in parsed.directives:
            if self.matches(term, domain):
                return _QUALIFIER_RESULTS[term.qualifier]

        if parsed.redirect is not None and not parsed.has_all:
            self.count_term()
            target = self.expand(parsed.redirect, domain)
            result = self.check_host(target)
            if result == NONE:
                raise SPFPermError(f"redirect={target} has no SPF record")
            return result
        return NEUTRAL


def _domain_is_valid(domain: str) -> bool:
    """4.3: a domain that cannot be queried yields ``none``."""
    name = domain.rstrip(".")
    if not name or len(name) > _MAX_DOMAIN_LENGTH or "." not in name:
        return False
    if name.startswith("["):
        return False
    return all(0 < len(label) <= 63 for label in name.split("."))


def check_host(
    ip: str,
    domain: str,
    sender: str,
    helo: str = "",
    lookup: Optional[SPFLookup] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[str, str]:
    """RFC 7208 ``check_host(<ip>, <domain>, <sender>)``.

    Returns ``(result, reason)``. Never raises for a sender-controlled input.
    """
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return NONE, f"invalid client address {ip!r}"
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped

    # 4.3: a sender without a local part is "postmaster@domain".
    if sender.startswith("@"):
        sender = f"postmaster{sender}"

    evaluation = _Evaluation(
        address,
        sender,
        helo,
        lookup or SPFLookup(),
        time.monotonic() + timeout,
    )
    try:
        return evaluation.check_host(domain), ""
    except SPFPermError as exc:
        return PERMERROR, str(exc)
    except SPFTempError as exc:
        return TEMPERROR, str(exc)


def evaluate_spf(
    ip: str,
    mail_from: str,
    helo: str,
    lookup: Optional[SPFLookup] = None,
) -> SPFResult:
    """SPF for the MAIL FROM identity, or HELO for a null sender (2.4)."""
    identity, domain = spf_identity(mail_from, helo)
    if not domain:
        reason = "no HELO identity" if identity == "helo" else "MAIL FROM has no domain"
        return SPFResult(NONE, None, identity, reason)
    sender = f"postmaster@{domain}" if identity == "helo" else mail_from.strip()
    result, reason = check_host(ip, domain, sender, helo=helo, lookup=lookup)
    return SPFResult(result, domain, identity, reason)


def spf_identity(mail_from: str, helo: str) -> tuple[str, Optional[str]]:
    """The identity SPF checks and its domain, as ``SPFResult`` reports them.

    MAIL FROM, or the HELO name for a null sender (2.4). "@example.com" (no
    local part) still names a domain, checked as postmaster (4.3).
    """
    mail_from = (mail_from or "").strip()
    if mail_from in ("", "<>"):
        identity, name = "helo", helo or ""
    else:
        identity = "mailfrom"
        name = address_domain(mail_from) or (
            mail_from[1:] if mail_from.startswith("@") else ""
        )
    # A-label, as the From domain is: an SMTPUTF8 sender may give a U-label,
    # and the two must compare equal for alignment.
    return identity, normalize_domain(name).rstrip(".") or None


@dataclasses.dataclass(frozen=True)
class SPFCheck:
    """An SPF check DMARC runs only when its result can matter."""

    domain: Optional[str]
    run: Callable[[], SPFResult]
