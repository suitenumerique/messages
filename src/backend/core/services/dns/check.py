"""
DNS checking functionality for mail domains.
"""

import collections
import ipaddress
import logging
import re
from typing import Dict, List, Optional, Tuple

from django.core.cache import cache

import dns.resolver
from sentry_sdk import capture_exception

from core.models import MailDomain

logger = logging.getLogger(__name__)

SPF_CHECK_CACHE_KEY_PREFIX = "dns:spf_check:"
SPF_CHECK_CACHE_TIMEOUT = 600  # 10 minutes

# DKIM tags whose values are base64: internal whitespace is not significant.
DKIM_BASE64_TAGS = frozenset({"p", "b", "bh"})
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


def is_spf_record(value: str) -> bool:
    """Whether a TXT value is an SPF record, per the RFC 7208 4.5 rules."""
    return SPF_VERSION_RE.match(value) is not None


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


def _dkim_tag_equal(tag: str, expected: str, found: str) -> bool:
    """Compare a single DKIM tag value.

    Per RFC 6376, whitespace inside a base64 value must be ignored. It is
    significant for other tags, so this cannot be applied globally.
    """
    if tag in DKIM_BASE64_TAGS:
        return "".join(expected.split()) == "".join(found.split())
    return expected == found


def _check_dkim_semantic(
    expected_value: str, found_values: List[str]
) -> Optional[Dict[str, any]]:
    """Semantic comparison for DKIM records (tag order doesn't matter per RFC 6376)."""
    expected_tags = parse_dkim_tags(expected_value)
    if not expected_tags:
        return None
    for found_value in found_values:
        found_tags = parse_dkim_tags(found_value)
        if not found_tags:
            continue
        if not all(
            k in found_tags and _dkim_tag_equal(k, v, found_tags[k])
            for k, v in expected_tags.items()
        ):
            continue
        # Check for t=y (testing mode) → insecure
        if found_tags.get("t") and "y" in found_tags["t"].split(":"):
            return {"status": "insecure", "found": found_values}
        return {"status": "correct", "found": found_values}
    return None


def _check_spf(expected_value: str, found_values: List[str]) -> Dict[str, any]:
    """SPF check: verify expected includes resolve, fall back to terms comparison."""
    expected = parse_spf_terms(expected_value)
    if not expected:
        return {"status": "incorrect", "found": found_values}

    expected_all, expected_terms = expected
    expected_includes = set(_extract_include_domains(expected_value))

    # Check there's at least one valid SPF record in found values
    found_spf_values = [v for v in found_values if is_spf_record(v)]
    if not found_spf_values:
        return {"status": "missing", "found": found_values}

    # A record receivers permerror on delegates nothing, whatever it lists.
    # This is deliberately kept apart from is_spf_record: RFC 7208 4.5 selects
    # records on the version section alone, before 4.6 validates them, so a
    # malformed record still counts towards the duplicate check.
    if not all(spf_syntax_is_valid(v) for v in found_spf_values):
        return {"status": "incorrect", "found": found_values}

    # If there are expected includes, check they resolve via BFS.
    # This is the primary signal: includes being set up is what matters.
    if expected_includes:
        resolved, visited, transient, error = _resolve_spf_includes(found_spf_values)
        if not expected_includes <= resolved:
            # A problem met while walking the chain only matters when it is
            # what kept our own include out of reach: a third party
            # duplicating its record, or a chain too long past our include,
            # says nothing about the record we asked the customer to publish.
            # A branch that failed transiently is unexplored, so unless every
            # include we expect was looked up and settled, it may sit there.
            if transient and not expected_includes <= visited - transient:
                return {
                    "status": "error",
                    "error": "DNS query failed while following the SPF chain",
                    "found": found_values,
                }
            if error and error.startswith("duplicate:"):
                return {"status": "duplicate", "found": found_values}
            return {"status": "incorrect", "found": found_values}
        # Includes resolve — check if "all" mechanism is acceptable
        if _found_all_matches(expected_all, found_spf_values):
            return {"status": "correct", "found": found_values}
        return {"status": "insecure", "found": found_values}

    # No includes: direct terms comparison (order-independent, ~all accepted for -all)
    for found_value in found_spf_values:
        found_all, found_terms = parse_spf_terms(found_value)
        if expected_terms <= found_terms:
            if _all_is_acceptable(expected_all, found_all):
                return {"status": "correct", "found": found_values}
            return {"status": "insecure", "found": found_values}

    return {"status": "incorrect", "found": found_values}


def _all_is_acceptable(expected_all: Optional[str], found_all: Optional[str]) -> bool:
    """Whether a found "all" mechanism is at least as strict as expected.

    A record with no "all" is read as the implicit "?all" it ends in, on
    either side: without it, an expected value carrying no "all" of its own
    could never be matched. "~all" also passes for an expected "-all":
    softfail is where most domains start, and it does not keep us from
    sending.
    """
    expected_all = expected_all or SPF_IMPLICIT_ALL
    found_all = found_all or SPF_IMPLICIT_ALL
    if expected_all == found_all:
        return True
    if expected_all == "-all" and found_all == "~all":
        return True
    return SPF_ALL_STRICTNESS[found_all] > SPF_ALL_STRICTNESS[expected_all]


def _found_all_matches(expected_all: str, found_values: List[str]) -> bool:
    """Check if any found SPF record has an acceptable "all" mechanism."""
    for found_value in found_values:
        found = parse_spf_terms(found_value)
        if not found:
            continue
        found_all, _ = found
        if _all_is_acceptable(expected_all, found_all):
            return True
    return False


def _extract_include_domains(spf_value: str) -> List[str]:
    """Extract the domains an SPF record delegates to, preserving order.

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


def _resolve_spf_includes(
    found_values: List[str], max_lookups: int = 10, max_void_lookups: int = 2
) -> Tuple[set, set, set, Optional[str]]:
    """BFS through SPF include chains, return all domains with valid SPF records.

    Seeds from the domains found_values delegate to, follows the chain via BFS.
    Per RFC 7208 4.6.4, stops after max_lookups DNS lookups, and after
    max_void_lookups of those came back with nothing. Both caps are what keeps
    a record from turning us into a DNS amplifier aimed at whatever names it
    lists, which need not even exist (RFC 7208 11.1).

    Returns:
        (resolved_domains, visited_domains, transient_failures, error) where
        visited_domains are the ones we got to look up, transient_failures the
        ones whose lookup failed in a way that may well succeed next time, and
        error is None on success, or a string describing the first problem met
        ("limit_reached", "void_limit_reached", "duplicate:domain.com").
    """
    queue = collections.deque()
    for found_value in found_values:
        if is_spf_record(found_value):
            queue.extend(_extract_include_domains(found_value))

    visited = set()
    resolved = set()
    transient = set()
    lookup_count = 0
    void_count = 0
    # Kept aside rather than returned on the spot, so that a dead end on one
    # branch neither hides an earlier problem nor cuts the walk short.
    error = None

    while queue:
        if lookup_count >= max_lookups:
            return resolved, visited, transient, error or "limit_reached"

        include_domain = queue.popleft()
        if include_domain in visited:
            continue
        visited.add(include_domain)
        lookup_count += 1

        try:
            answers = dns.resolver.resolve(include_domain, "TXT")
            spf_records = [
                value
                for value in (_txt_record_value(rr) for rr in answers.rrset)
                if is_spf_record(value)
            ]
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            # An include pointing at a name that publishes nothing is a
            # settled answer, not a failure to look it up — but it is a "void
            # lookup", and a run of them is the amplification RFC 7208 4.6.4
            # caps. A name that answers without an SPF record is not one: the
            # query did come back with something.
            logger.debug("No TXT record for %s", include_domain)
            void_count += 1
            if void_count > max_void_lookups:
                return resolved, visited, transient, error or "void_limit_reached"
            continue
        except (dns.resolver.Timeout, dns.resolver.NoNameservers):
            logger.debug("DNS resolution failed for %s, may retry", include_domain)
            transient.add(include_domain)
            continue
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Unexpected error resolving %s: %s", include_domain, exc, exc_info=True
            )
            capture_exception(exc)
            continue

        if len(spf_records) > 1:
            # A name publishing two records permerrors (RFC 7208 4.5), so this
            # include delegates nothing — the same dead end a malformed record
            # below is, and walked past the same way. Only a third party can
            # be one: the customer's own duplicates never reach here, they are
            # caught before the walk starts. Stopping would let where a third
            # party sits in the record decide whether we find our own include.
            logger.debug("Duplicate SPF records at %s", include_domain)
            error = error or f"duplicate:{include_domain}"
            continue

        if not spf_records:
            continue

        # Every version-matching record counted towards the duplicate check
        # above, per the RFC 7208 4.5 selection rules; only now that one has
        # been selected does 4.6 syntax apply. A record receivers permerror
        # on makes the include return permerror too (5.2), so it delegates
        # nothing and leads nowhere.
        if not spf_syntax_is_valid(spf_records[0]):
            logger.debug("Malformed SPF record at %s", include_domain)
            continue

        resolved.add(include_domain)
        for child_domain in _extract_include_domains(spf_records[0]):
            if child_domain not in visited:
                queue.append(child_domain)

    return resolved, visited, transient, error


def _txt_record_value(rr) -> str:
    """Normalized value of a single TXT resource record.

    RFC 7208 3.3 and RFC 6376 3.6.2: the strings of one record are
    concatenated with no separator, which is how a value over the 255-octet
    limit on a character-string is published. Separate TXT records arrive as
    separate resource records, so several strings here always belong to the
    same record. SPF records are US-ASCII (RFC 7208 3.1), but an unrelated
    TXT record may hold anything, and that is no reason to fail the check.
    """
    return normalize_txt_value(b"".join(rr.strings).decode(errors="replace"))


def _resolve_dns_values(record_type, query_name):
    """Resolve DNS and return found values and normalized expected value flag."""
    if record_type.upper() == "MX":
        answers = dns.resolver.resolve(query_name, "MX")
        return [f"{answer.preference} {answer.exchange}" for answer in answers]

    if record_type.upper() == "TXT":
        answers = dns.resolver.resolve(query_name, "TXT")
        return [_txt_record_value(rr) for rr in answers.rrset]

    answers = dns.resolver.resolve(query_name, record_type)
    return [answer.to_text() for answer in answers]


def _check_txt_security(expected_value, found_values):
    """Check for duplicate/insecure SPF and DMARC records. Returns result or None."""
    # SPF duplicate check. Whether the policy is strong enough is left to
    # _check_spf: a weak "all" only reads as "insecure" once the delegation
    # is known to be in place, and "insecure" is a status we still send on.
    if is_spf_record(expected_value):
        if len([v for v in found_values if is_spf_record(v)]) > 1:
            return {"status": "duplicate", "found": found_values}

    # DMARC duplicate and insecure checks
    if expected_value.startswith("v=DMARC1"):
        dmarc_records = [v for v in found_values if v.startswith("v=DMARC1")]
        if len(dmarc_records) > 1:
            return {"status": "duplicate", "found": found_values}
        if "p=none" not in expected_value:
            for dmarc in dmarc_records:
                if "p=none" in dmarc:
                    return {"status": "insecure", "found": found_values}

    return None


def check_single_record(
    maildomain: MailDomain, expected_record: Dict[str, any]
) -> Dict[str, any]:
    """
    Check a single DNS record for a mail domain.

    Args:
        maildomain: The MailDomain instance
        expected_record: The expected record to check

    Returns:
        Check result dictionary with status and details
    """
    record_type = expected_record["type"]
    target = expected_record["target"]
    expected_value = expected_record["value"]

    # Build the query name
    query_name = f"{target}.{maildomain.name}" if target else maildomain.name

    try:
        found_values = _resolve_dns_values(record_type, query_name)
        if record_type.upper() == "TXT":
            expected_value = normalize_txt_value(expected_value)

        # Check for duplicate/insecure SPF and DMARC
        if record_type.upper() == "TXT":
            security_result = _check_txt_security(expected_value, found_values)
            if security_result:
                return security_result

        # SPF: always use semantic check (handles exact match, reordering,
        # ~all acceptance, and recursive include verification)
        if record_type.upper() == "TXT" and is_spf_record(expected_value):
            return _check_spf(expected_value, found_values)

        # Exact match (non-SPF)
        if expected_value in found_values:
            return {"status": "correct", "found": found_values}

        # Semantic fallback for DKIM
        if record_type.upper() == "TXT" and target.endswith("._domainkey"):
            result = _check_dkim_semantic(expected_value, found_values)
            if result:
                return result

        return {"status": "incorrect", "found": found_values}

    except dns.resolver.NXDOMAIN:
        return {"status": "missing", "error": "Domain not found"}
    except dns.resolver.NoAnswer:
        return {"status": "missing", "error": "No records found"}
    except dns.resolver.NoNameservers:
        return {"status": "missing", "error": "No nameservers found"}
    except dns.resolver.Timeout:
        return {"status": "error", "error": "DNS query timeout"}
    except dns.resolver.YXDOMAIN:
        return {"status": "error", "error": "Domain name too long"}
    except Exception as e:  # pylint: disable=broad-exception-caught
        return {"status": "error", "error": f"DNS query failed: {str(e)}"}


def _spf_check_cache_key(maildomain: MailDomain) -> str:
    return f"{SPF_CHECK_CACHE_KEY_PREFIX}{maildomain.pk}"


def check_spf_status(maildomain: MailDomain) -> bool:
    """Check if the SPF include chain is correctly set up for a mail domain.

    Results are cached for 10 minutes. Returns True if SPF is correct
    (or if no SPF record is expected), False otherwise.
    """
    cache_key = _spf_check_cache_key(maildomain)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result, is_definitive = _check_spf_status_uncached(maildomain)
    if is_definitive:
        cache.set(cache_key, result, SPF_CHECK_CACHE_TIMEOUT)
    return result


def _check_spf_status_uncached(maildomain: MailDomain) -> Tuple[bool, bool]:
    """Perform the actual SPF check (no cache).

    Returns:
        (is_correct, is_definitive) where is_definitive is False when the
        result was caused by a transient DNS error (timeout, no nameservers).
    """
    expected_records = maildomain.get_expected_dns_records()
    spf_records = [
        r
        for r in expected_records
        # check_single_record normalizes before matching, so a configured
        # value carrying its zone-file quotes has to be recognized here too.
        if r["type"].upper() == "TXT" and is_spf_record(normalize_txt_value(r["value"]))
    ]
    if not spf_records:
        return True, True

    for expected_record in spf_records:
        result = check_single_record(maildomain, expected_record)
        status = result.get("status")
        if status not in ("correct", "insecure"):
            is_transient = status == "error"
            return False, not is_transient

    return True, True


def invalidate_spf_check_cache(maildomain: MailDomain) -> None:
    """Clear the cached SPF check result for a mail domain."""
    cache.delete(_spf_check_cache_key(maildomain))


def check_dns_records(maildomain: MailDomain) -> List[Dict[str, any]]:
    """
    Check DNS records for a mail domain against expected records.

    Args:
        maildomain: The MailDomain instance to check

    Returns:
        List of records with their check status
    """
    expected_records = maildomain.get_expected_dns_records()
    results = []

    # Collect expected MX values for conflicting detection
    expected_mx_values = {
        record["value"] for record in expected_records if record["type"].upper() == "MX"
    }

    for expected_record in expected_records:
        result_record = expected_record.copy()
        result_record["_check"] = check_single_record(maildomain, expected_record)

        # For MX records that are correct, check for extra (conflicting) MX entries
        if (
            expected_record["type"].upper() == "MX"
            and result_record["_check"]["status"] == "correct"
        ):
            found = result_record["_check"].get("found", [])
            extra_mx = [v for v in found if v not in expected_mx_values]
            if extra_mx:
                result_record["_check"]["status"] = "conflicting"

        results.append(result_record)

    return results
