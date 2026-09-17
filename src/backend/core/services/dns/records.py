"""Pure parsing of the TXT records mail authentication uses.

Deliberately free of Django and model imports: both the DNS check
(``core.services.dns.check``) and DKIM verification (``core.mda.signing``)
need this, and ``core.models`` imports ``core.mda.signing``, so anything
these two share has to live somewhere that imports neither.

DMARC here is RFC 7489, which RFC 9989 obsoleted in May 2026. The record
syntax is unchanged — same ``v=DMARC1``, same tag-value list, same ``p``
values — so everything parsed below reads a 9989 record correctly, and the
section numbers just move (6.3 -> 4.7, 6.4 -> 4.8, 6.6.3 -> 4.10.1).

What 9989 adds is not implemented: the DNS Tree Walk that replaces the Public
Suffix List for organizational-domain discovery (see
``core.mda.addresses.organizational_domain``), and the ``psd``, ``np`` and
``t`` tags. Unknown tags must be ignored per 6.3, so a record carrying them
parses fine here — we simply do not act on them. Receiver-side adoption of
the walk was still marginal when this was written, and every implementation
we interoperate with, rspamd included, is on the PSL.
"""

import collections
import ipaddress
import re
import string
from typing import Dict, List, Optional, Tuple

DKIM_VERSION = "DKIM1"
DKIM_DEFAULT_KEY_TYPE = "rsa"
# RFC 6376 3.2 spells tag-name as ALPHA *ALNUMPUNC. Only the leading ALPHA is
# enforced, matching dkimpy: vendor tags such as "x-foo" are used in the wild
# and verify fine, so rejecting them would report working records as broken.
DKIM_TAG_NAME_RE = re.compile(r"[a-zA-Z]")

SPF_VERSION = "v=spf1"
# RFC 7208 4.5: a record starts with a version section of exactly "v=spf1",
# terminated by a space or the end of the record, so "v=spf10" is not one.
# Per Section 12, ABNF literals are case-insensitive: "V=sPf1" is. Only a
# space terminates it: RFC 7208 4.6.1 separates terms with SP alone, and a
# record broken by another control character is one receivers reject too.
# Records are US-ASCII (3.1) and receivers compare bytes, so the folding has
# to stay ASCII as well: Unicode maps U+017F onto "s" and U+212A onto "k".
SPF_VERSION_RE = re.compile(rf"{SPF_VERSION}( |$)", re.IGNORECASE | re.ASCII)
# RFC 7208 4.6.1: directive = [ qualifier ] mechanism.
SPF_QUALIFIERS = "+-?~"
# RFC 7208 4.6.1: a term name ends at the first ":", "=" or "/".
SPF_TERM_NAME_RE = re.compile(r"[^:=/]*")
# RFC 7208 5: the complete set of mechanisms. SPF has no extension point for
# new ones, so a term that is neither one of these nor a "name=value"
# modifier is a syntax error.
SPF_MECHANISMS = frozenset({"all", "include", "a", "mx", "ptr", "ip4", "ip6", "exists"})
# RFC 7208 12: "include", "exists", "ip4" and "ip6" spell a mandatory ":"
# argument, "all" takes none, and "a", "mx" and "ptr" take an optional one.
SPF_MECHANISMS_NEEDING_ARGUMENT = frozenset({"include", "exists", "ip4", "ip6"})
# The only two modifiers RFC 7208 defines: each may appear at most once (6),
# and each takes a domain-spec, which is never empty (6.1 and 6.2). Any other
# modifier must be ignored, however often it appears and even if it is empty.
SPF_DEFINED_MODIFIERS = ("redirect", "exp")
# RFC 7208 12: name = ALPHA *( ALPHA / DIGIT / "-" / "_" / "." ), where ALPHA
# is ASCII, so the folding is held to ASCII as it is for the version above.
SPF_MODIFIER_NAME_RE = re.compile(r"[a-z][a-z0-9_.-]*", re.IGNORECASE | re.ASCII)
# RFC 7208 12: ip4-cidr-length is 0-32 and ip6-cidr-length 0-128.
SPF_MAX_CIDR_LENGTH = {"ip4": 32, "ip6": 128}
# Ordered from most permissive to strictest (RFC 7208 4.6.2).
SPF_ALL_STRICTNESS = {"+all": 0, "?all": 1, "~all": 2, "-all": 3}
SPF_ALL_MECHANISMS = frozenset(SPF_ALL_STRICTNESS)
# RFC 7208 4.7: a record with no "all" ends in an implicit "?all".
SPF_IMPLICIT_ALL = "?all"

DMARC_VERSION = "DMARC1"
# RFC 7489 6.4: dmarc-version = "v" *WSP "=" *WSP %x44 %x4d %x41 %x52 %x43 %x31.
# The "v" is an ABNF quoted literal and so case-insensitive (RFC 5234 2.3),
# while DMARC1 is spelled as explicit octets and is not — the reverse of SPF's
# "v=spf1", where the whole literal folds. *WSP is space or tab, never a
# newline, so "v = DMARC1" is a record and a line-broken one is not. The
# terminator is the ";" of dmarc-sep, or the end: without it "v=DMARC1000"
# reads as a DMARC record. End-of-string is accepted because 6.6.3 selects on
# the version tag alone, before anything validates the rest.
DMARC_VERSION_RE = re.compile(rf"[vV][ \t]*=[ \t]*{DMARC_VERSION}([ \t]*;|$)", re.ASCII)
# RFC 7489 6.3, ordered from weakest to strongest request.
DMARC_POLICY_STRICTNESS = {"none": 0, "quarantine": 1, "reject": 2}
# RFC 7489 6.3: alignment defaults to relaxed, so an absent tag is "r" and a
# published "r" is no weaker than an absent one.
DMARC_ALIGNMENT_STRICTNESS = {"r": 0, "s": 1}
DMARC_DEFAULT_ALIGNMENT = "r"
DMARC_STRICT_ALIGNMENT = "s"
# The tags whose values RFC 7489 6.4 enumerates. Everything else — rua, ruf,
# fo, and anything a later revision adds — is free-form to us: 6.3 requires
# unknown tags be ignored, so validating them would reject records receivers
# accept.
DMARC_ENUMERATED_TAGS = {
    "p": frozenset(DMARC_POLICY_STRICTNESS),
    "sp": frozenset(DMARC_POLICY_STRICTNESS),
    "adkim": frozenset(DMARC_ALIGNMENT_STRICTNESS),
    "aspf": frozenset(DMARC_ALIGNMENT_STRICTNESS),
}
# RFC 7489 6.4 spells every tag name as ALPHA followed by alphanumerics.
DMARC_TAG_NAME_RE = re.compile(r"[a-z][a-z0-9]*", re.ASCII)

# Same policy as ``core.mda.addresses.ascii_lower``, kept local so this module
# stays free of the Django imports that one pulls in. Folding is restricted to
# A-Z because Unicode maps U+017F onto "s" and U+212A onto "k", which would let
# a record that is not a policy compare equal to one that is.
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _ascii_lower(value: str) -> str:
    """Lowercase ``A-Z``, leaving every other code point untouched."""
    return value.translate(_ASCII_LOWER)


def is_spf_record(value: str) -> bool:
    """Whether a TXT value is an SPF record, per the RFC 7208 4.5 rules."""
    return SPF_VERSION_RE.match(value) is not None


def is_dmarc_record(value: str) -> bool:
    """Whether a TXT value is a DMARC record, per the RFC 7489 6.6.3 rules."""
    return DMARC_VERSION_RE.match(value) is not None


def _parse_spf_term(term: str) -> Tuple[str, str, str]:
    """Split an SPF term into (qualifier, name, argument).

    A directive is an optional "+"/"-"/"?"/"~" qualifier followed by a
    mechanism, while a modifier is a bare "name=value" and takes no qualifier
    (RFC 7208 4.6.1). Names are case-insensitive, and so are the domains they
    carry; an argument holding a macro keeps its case, since "%{s}" and "%{S}"
    do not expand the same way. The argument keeps its separator.
    """
    qualifier = ""
    if term and term[0] in SPF_QUALIFIERS:
        qualifier, term = term[0], term[1:]
    name = SPF_TERM_NAME_RE.match(term).group()
    argument = term[len(name) :]
    if "%" not in argument:
        argument = argument.lower()
    return qualifier, name.lower(), argument


def _canonical_spf_term(term: str) -> str:
    """Canonical form of an SPF term, so that case and an implicit "+" (which
    is what an omitted qualifier means) do not make equivalent terms differ."""
    qualifier, name, argument = _parse_spf_term(term)
    if argument.startswith("="):  # a modifier, which takes no qualifier
        return f"{name}{argument}"
    return f"{qualifier or '+'}{name}{argument}"


def parse_spf_terms(value: str) -> Optional[Tuple[str, set]]:
    """Parse an SPF record into its qualified "all" and set of other terms.

    Returns (all_mechanism, other_terms) where all_mechanism is the canonical
    "-all", "~all", "+all" or "?all" (a bare "all" means "+all"), or None when
    the record has no "all" at all. Only the first one counts: "all" always
    matches, so anything after it is never tested (RFC 7208 5.1). Terms are
    canonicalized, so ordering, letter case and implicit qualifiers do not
    matter.
    Returns None if not a valid SPF record.
    """
    if not is_spf_record(value):
        return None
    all_mechanism = None
    other_terms = set()
    for term in value[len(SPF_VERSION) :].split():
        canonical = _canonical_spf_term(term)
        if canonical in SPF_ALL_MECHANISMS:
            if all_mechanism is None:
                all_mechanism = canonical
        # Mechanisms after the first "all" are never tested (RFC 7208 5.1).
        # Modifiers are not mechanisms and still apply (4.6.3): a canonical
        # mechanism always carries its qualifier, a modifier never does.
        elif all_mechanism is None or canonical[0] not in SPF_QUALIFIERS:
            other_terms.add(canonical)
    return (all_mechanism, other_terms)


def _cidr_length_is_valid(length: str, maximum: int) -> bool:
    """Whether a CIDR length is spelled as RFC 7208 12 requires.

    Either "0" or a digit string that does not start with one, within the
    maximum for its address family. Anything else left by the split — a
    second "/", an empty string — is not a length at all.
    """
    if not length.isdigit() or (length.startswith("0") and length != "0"):
        return False
    return int(length) <= maximum


def _ip_network_is_valid(name: str, argument: str) -> bool:
    """Whether an "ip4:" or "ip6:" argument is an address and CIDR length.

    Splitting on "/" is only unambiguous here: an ip-network is a literal, so
    unlike the domain-spec of "a" or "mx" it cannot carry a macro with a "/"
    of its own. RFC 7208 12 allows a single length, never the dual "//" form.
    """
    literal, separator, cidr_length = argument[1:].partition("/")
    if separator and not _cidr_length_is_valid(cidr_length, SPF_MAX_CIDR_LENGTH[name]):
        return False
    try:
        address = ipaddress.ip_address(literal)
    except ValueError:
        return False
    return (address.version == 4) == (name == "ip4")


def _dual_cidr_is_valid(argument: str) -> bool:
    """Whether an "a" or "mx" argument that is only a dual-cidr-length is in
    range: [ ip4-cidr-length ] [ "/" ip6-cidr-length ] (RFC 7208 12).

    Unambiguous exactly because no domain-spec precedes it here; once one
    does, it may carry a macro holding a "/" of its own.
    """
    ip4_part, has_ip6, ip6_length = argument.partition("//")
    if has_ip6 and not _cidr_length_is_valid(ip6_length, SPF_MAX_CIDR_LENGTH["ip6"]):
        return False
    return not ip4_part or _cidr_length_is_valid(
        ip4_part[1:], SPF_MAX_CIDR_LENGTH["ip4"]
    )


def spf_syntax_is_valid(value: str) -> bool:
    """Whether an SPF record's terms are well formed.

    A syntax error anywhere makes receivers return permerror for the whole
    record (RFC 7208 4.6), so the delegation it describes never takes effect.
    Term names, the presence of their arguments and the ip4/ip6 literals are
    checked. A domain-spec is not: it may hold macros that expand at
    evaluation time, and rejecting one wrongly would report a working record
    as broken. That leaves the CIDR lengths of "a" and "mx" unchecked too,
    since their domain-spec can contain the "/" they would be split on.
    """
    if not is_spf_record(value):
        return False
    modifiers = collections.Counter()
    for term in value[len(SPF_VERSION) :].split():
        qualifier, name, argument = _parse_spf_term(term)
        if argument.startswith("="):
            # A modifier is a bare "name=value" (4.6.1): a qualifier belongs
            # to a directive, so a qualified one is neither. Unrecognized
            # modifiers must still be ignored whatever they say (6), so only
            # their name, and the domain-spec of the two defined ones, matter.
            if qualifier or not SPF_MODIFIER_NAME_RE.fullmatch(name):
                return False
            if name in SPF_DEFINED_MODIFIERS and argument == "=":
                return False
            modifiers[name] += 1
            continue
        if name not in SPF_MECHANISMS:
            return False
        if name in SPF_MECHANISMS_NEEDING_ARGUMENT and not argument.startswith(":"):
            return False
        if name == "all" and argument:
            return False
        if argument == ":":
            return False
        if name in SPF_MAX_CIDR_LENGTH and not _ip_network_is_valid(name, argument):
            return False
        if name in ("a", "mx") and argument.startswith("/"):
            if not _dual_cidr_is_valid(argument):
                return False
    return all(modifiers[name] <= 1 for name in SPF_DEFINED_MODIFIERS)


def spf_delegated_domains(spf_value: str) -> List[str]:
    """The domains an SPF record hands the decision to, in order.

    Both the "include:" mechanism and the "redirect=" modifier hand the
    decision over to another domain's record (RFC 7208 5.2 and 6.1), so a
    domain reached either way counts. An "all" mechanism ends that though:
    it always matches, so an include listed after it is never tested (5.1),
    and a redirect is inoperative when an "all" appears anywhere at all in
    the record (5.1 and 6.1), wherever the two sit relative to each other.
    """
    includes = []
    redirects = []
    has_all = False
    for term in spf_value.split():
        qualifier, name, argument = _parse_spf_term(term)
        if name == "all" and not argument:
            has_all = True
        elif name == "include" and argument.startswith(":") and not has_all:
            includes.append(argument[1:])
        elif name == "redirect" and not qualifier and argument.startswith("="):
            redirects.append(argument[1:])
    # A redirect is only reached once every mechanism failed to match, so it
    # comes last. A macro only expands at evaluation time, so it names no
    # domain we could look up here.
    domains = includes if has_all else includes + redirects
    return [domain for domain in domains if domain and "%" not in domain]


def normalize_txt_value(value: str) -> str:
    """
    Normalize a TXT record value.

    Only a lone trailing semicolon is dropped, so that it does not defeat
    exact comparison. A repeated one is kept: it makes a DKIM tag-list
    invalid (RFC 6376 3.2) and must not normalize into a valid record.
    """
    return re.sub(r"(?<!;);$", "", re.sub(r"\s*\;\s*", ";", value.strip('"')))


def parse_dkim_tags(value: str) -> Optional[Dict[str, str]]:
    """Parse a DKIM key record into a dict of tag=value pairs.

    Per RFC 6376 3.2, tags are separated by semicolons and folding whitespace
    is allowed on both sides of the "=". Every tag-spec must be a named
    tag=value pair, and a duplicate tag name invalidates the whole tag-list.
    Per 3.6.1, v= is optional and defaults to DKIM1, but MUST be first and
    equal to DKIM1 when present.
    Returns None if the record is not a valid DKIM key record.
    """
    specs = value.split(";")
    # A single trailing semicolon is allowed; any other empty tag-spec
    # (leading, interior, or a second trailing one) makes the record invalid.
    if len(specs) > 1 and not specs[-1].strip():
        specs.pop()
    tags = {}
    for spec in specs:
        part = spec.strip()
        if "=" not in part:
            return None
        key, val = part.split("=", 1)
        key = key.strip()
        if not DKIM_TAG_NAME_RE.match(key) or key in tags:
            return None
        tags[key] = val.strip()
    if "v" in tags:
        if tags["v"] != DKIM_VERSION or specs[0].split("=", 1)[0].strip() != "v":
            return None
    else:
        tags["v"] = DKIM_VERSION
    # RFC 6376 3.6.1: k= is optional and defaults to rsa. Records omitting it
    # are common, so applying the default keeps them from reading as a
    # mismatch against the k= we publish.
    tags.setdefault("k", DKIM_DEFAULT_KEY_TYPE)
    return tags


def parse_dmarc_tags(value: str) -> Optional[Dict[str, str]]:
    """Parse a DMARC record into a dict of tag=value pairs.

    Per RFC 7489 6.4, tags are separated by ";" with folding whitespace allowed
    on both sides of the "=", and tag names are ABNF quoted literals and so
    case-insensitive. Values are returned as spelled: only the caller knows
    which of them fold (6.3 defines "p" as a literal too, "rua" as a URI list).

    Deliberately lenient past the version tag: 6.3 requires unknown tags be
    ignored, and a tag-spec we cannot read is skipped rather than failing the
    record. Selection already happened in :func:`is_dmarc_record`, so a broken
    unrelated tag must not hide the policy the record does state. The first
    occurrence of a repeated tag wins, as the first "all" does in SPF.
    Returns None if the value is not a DMARC record at all.
    """
    if not is_dmarc_record(value):
        return None
    tags: Dict[str, str] = {}
    for spec in value.split(";"):
        name, separator, tag_value = spec.partition("=")
        if not separator:
            continue
        name = _ascii_lower(name.strip())
        if name and name not in tags:
            tags[name] = tag_value.strip()
    return tags


def dmarc_policy(value: str) -> str:
    """The policy a DMARC record requests for the domain itself.

    RFC 7489 6.3 makes "p" mandatory for a policy record and defines exactly
    three values for it. A record carrying neither, or one 6.4 does not
    define, requests nothing enforceable, which is read here as the weakest
    policy so that it can never satisfy a stronger expectation.

    "sp" is deliberately not consulted: it governs subdomains (6.3), and a
    record may well pair a strict "p" with a relaxed "sp". Reading the two as
    one policy would report ``p=reject; sp=none`` — a strict domain policy —
    as insecure, so the tag has to be read, not searched for as a substring.
    """
    tags = parse_dmarc_tags(value) or {}
    policy = _ascii_lower(tags.get("p", ""))
    return policy if policy in DMARC_POLICY_STRICTNESS else "none"


def dmarc_alignment(value: str, tag: str) -> str:
    """The alignment mode a DMARC record requests for ``adkim`` or ``aspf``.

    Absent or unrecognized reads as "r": RFC 7489 6.3 makes relaxed the
    default, so a record that says nothing is asking for relaxed rather than
    for nothing.
    """
    tags = parse_dmarc_tags(value) or {}
    mode = _ascii_lower(tags.get(tag, ""))
    return mode if mode in DMARC_ALIGNMENT_STRICTNESS else DMARC_DEFAULT_ALIGNMENT


def dmarc_syntax_is_valid(value: str) -> bool:
    """Whether a DMARC record's tags are well formed.

    Structure first: every tag-spec has to be ``name=value`` with a name RFC
    7489 6.4 would accept, because a record receivers cannot parse enforces
    nothing however good its policy looks.

    Values are only checked for the four tags 6.4 enumerates. Everything else
    is left alone on purpose — 6.3 requires unknown tags be ignored, and a
    ``rua`` URI list or a tag from a later revision must not read as broken
    here when receivers accept it. Same trade the SPF validator makes with
    domain-specs.

    An empty tag-spec is tolerated: a trailing ";" is idiomatic and every
    parser in the wild accepts it.
    """
    if not is_dmarc_record(value):
        return False
    for spec in value.split(";"):
        if not spec.strip():
            continue
        name, separator, tag_value = spec.partition("=")
        if not separator:
            return False
        name = _ascii_lower(name.strip())
        if not DMARC_TAG_NAME_RE.fullmatch(name):
            return False
        permitted = DMARC_ENUMERATED_TAGS.get(name)
        if permitted is not None and _ascii_lower(tag_value.strip()) not in permitted:
            return False
    return True


def dmarc_subdomain_policy(value: str) -> str:
    """The policy a DMARC record requests for the domain's subdomains.

    RFC 7489 6.3: "sp" applies to subdomains and, when absent, they inherit
    "p". So a record publishing ``p=reject`` alone asks for reject on
    subdomains too, while ``p=reject; sp=none`` leaves them unprotected —
    which is why this is graded separately from :func:`dmarc_policy` rather
    than folded into it.
    """
    tags = parse_dmarc_tags(value) or {}
    subdomain = _ascii_lower(tags.get("sp", ""))
    return subdomain if subdomain in DMARC_POLICY_STRICTNESS else dmarc_policy(value)
