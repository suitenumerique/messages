"""
DNS checking functionality for mail domains.
"""

import collections
import logging
import time
from typing import Dict, List, Optional, Tuple

from django.core.cache import cache

from sentry_sdk import capture_exception

from core.models import MailDomain
from core.services.dns.records import (
    DMARC_ALIGNMENT_STRICTNESS,
    DMARC_POLICY_STRICTNESS,
    SPF_ALL_STRICTNESS,
    SPF_IMPLICIT_ALL,
    dmarc_alignment,
    dmarc_policy,
    dmarc_subdomain_policy,
    dmarc_syntax_is_valid,
    is_dmarc_record,
    is_spf_record,
    normalize_txt_value,
    parse_dkim_tags,
    parse_dmarc_tags,
    parse_spf_terms,
    spf_delegated_domains,
    spf_syntax_is_valid,
)
from core.services.dns.resolver import (
    DNSSECError,
    InvalidNameError,
    NoAnswerError,
    NXDOMAINError,
    ResolutionTimeoutError,
    ResolverError,
    ServfailError,
    resolve_answer,
)

logger = logging.getLogger(__name__)

SPF_CHECK_CACHE_KEY_PREFIX = "dns:spf_check:"
SPF_CHECK_CACHE_TIMEOUT = 600  # 10 minutes

# Ceiling on a whole domain check, which DNS_RESOLVER_MAX_RESOLUTION_TIME does
# not give: that one bounds a single name, and a check resolves the expected
# records plus up to ten SPF includes in sequence, so the worst case multiplies
# out past gunicorn's 90s request timeout on the synchronous check-dns
# endpoint. Records the budget does not reach report "error", the same
# not-definitive status a timeout gets, so it is never cached and never reads
# as a verdict on the domain's DNS.
#
# Not configurable on purpose. A check that runs this long has already failed
# at its job — nobody waits 30s for a page, and the send path re-checks per
# message — so there is no deployment that wants a higher value, only one that
# wants to know its DNS is this slow.
DNS_CHECK_TOTAL_TIMEOUT = 30.0

# DKIM tags whose values are base64: internal whitespace is not significant.
DKIM_BASE64_TAGS = frozenset({"p", "b", "bh"})


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


def _check_dmarc_semantic(
    expected_value: str, found_values: List[str]
) -> Optional[Dict[str, any]]:
    """Semantic comparison for DMARC records, as DKIM and SPF already get.

    Compared exactly, a DMARC record is judged on its spelling: adding the
    ``rua=`` every deployment guide asks for, reordering tags, or publishing
    the stronger ``sp=reject`` all read as "incorrect" while being correct or
    better. Only three things actually matter:

    - the policy is at least as strict as the one we asked for,
    - the alignment modes are at least as strict (absent means relaxed, per
      RFC 7489 6.3, so an omitted ``adkim`` is weaker than the ``adkim=s`` we
      publish),
    - any other tag we expect is present with the same value.

    Extra tags in the published record are fine — reporting addresses and
    subdomain policies are additions, not deviations. A weaker policy or
    alignment reads as "insecure" rather than "incorrect": the record works,
    it just protects less than we asked, which is the same distinction SPF's
    "all" handling draws.

    Assumes the found records already passed ``dmarc_syntax_is_valid``:
    ``_check_txt_security`` runs first for every TXT check and rejects a
    malformed one as "incorrect" before anything here grades it.
    """
    if not dmarc_syntax_is_valid(expected_value):
        return None
    for found_value in found_values:
        if not is_dmarc_record(found_value):
            continue
        weaker = (
            not _dmarc_policy_is_acceptable(
                dmarc_policy(expected_value), dmarc_policy(found_value)
            )
            or not _dmarc_policy_is_acceptable(
                dmarc_subdomain_policy(expected_value),
                dmarc_subdomain_policy(found_value),
            )
        ) or any(
            DMARC_ALIGNMENT_STRICTNESS[dmarc_alignment(found_value, tag)]
            < DMARC_ALIGNMENT_STRICTNESS[dmarc_alignment(expected_value, tag)]
            for tag in ("adkim", "aspf")
        )
        if weaker:
            return {"status": "insecure", "found": found_values}

        expected_tags = parse_dmarc_tags(expected_value) or {}
        found_tags = parse_dmarc_tags(found_value) or {}
        # "p", "sp" and the alignment tags were judged on strength above, so
        # comparing them again here would reject a record for being stronger.
        graded = {"p", "sp", "adkim", "aspf"}
        if any(
            found_tags.get(name) != value
            for name, value in expected_tags.items()
            if name not in graded
        ):
            return {"status": "incorrect", "found": found_values}
        return {"status": "correct", "found": found_values}
    return None


def _check_spf(
    expected_value: str, found_values: List[str], deadline: Optional[float] = None
) -> Dict[str, any]:
    """SPF check: verify expected includes resolve, fall back to terms comparison."""
    expected = parse_spf_terms(expected_value)
    if not expected:
        return {"status": "incorrect", "found": found_values}

    expected_all, expected_terms = expected
    expected_includes = set(spf_delegated_domains(expected_value))

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
        resolved, visited, transient, error = _resolve_spf_includes(
            found_spf_values, deadline=deadline
        )
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
                # Name the domain that actually publishes two records. The
                # customer's own duplicates were caught before the walk began,
                # so the one found here is somewhere along the chain, while
                # ``found`` below holds their apex TXT — a single, correct SPF
                # record. A bare "duplicate" against that would send the
                # operator to fix a record that is not the problem.
                return {
                    "status": "duplicate",
                    "error": (
                        f"{error.removeprefix('duplicate:')} publishes multiple "
                        f"SPF records, so the chain stops there"
                    ),
                    "found": found_values,
                }
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


def _resolve_spf_includes(
    found_values: List[str],
    max_lookups: int = 10,
    max_void_lookups: int = 2,
    deadline: Optional[float] = None,
) -> Tuple[set, set, set, Optional[str]]:
    """BFS through SPF include chains, return all domains with valid SPF records.

    Seeds from the domains found_values delegate to, follows the chain via BFS.
    Per RFC 7208 4.6.4, stops after max_lookups DNS lookups, and after
    max_void_lookups of those came back with nothing. Both caps are what keeps
    a record from turning us into a DNS amplifier aimed at whatever names it
    lists, which need not even exist (RFC 7208 11.1).

    ``deadline`` is a ``time.monotonic()`` value bounding the walk in wall
    clock, which the lookup caps do not: ten includes each allowed a full
    ``DNS_RESOLVER_MAX_RESOLUTION_TIME`` is minutes, and this runs inside a
    synchronous request. A domain left unwalked when it expires goes into
    ``transient`` — we did not learn what it publishes, which is exactly what
    that set means.

    Returns:
        (resolved_domains, visited_domains, transient_failures, error) where
        visited_domains are the ones we got to look up, transient_failures the
        ones whose lookup failed in a way that may well succeed next time, and
        error is None on success, or a string describing the first problem met
        ("limit_reached", "void_limit_reached", "deadline_exceeded",
        "duplicate:domain.com").
    """
    queue = collections.deque()
    for found_value in found_values:
        if is_spf_record(found_value):
            queue.extend(spf_delegated_domains(found_value))

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
        if deadline is not None and time.monotonic() >= deadline:
            # Everything still queued is unexplored, not absent: mark it so the
            # caller reports "error" rather than a verdict on the customer.
            transient.update(queue)
            logger.warning(
                "SPF chain walk hit its deadline with %d domain(s) unexplored",
                len(queue),
            )
            return resolved, visited, transient, error or "deadline_exceeded"

        include_domain = queue.popleft()
        if include_domain in visited:
            continue
        visited.add(include_domain)
        lookup_count += 1

        try:
            spf_records = [
                value
                for value in (
                    normalize_txt_value(v)
                    for v in resolve_answer(include_domain, "TXT").text_values()
                )
                if is_spf_record(value)
            ]
        except (NXDOMAINError, NoAnswerError):
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
        except InvalidNameError:
            # Settled, and not even a lookup: no query goes out for a name that
            # cannot be formed, so it is not a void lookup either. Kept out of
            # ``transient``, which would make the check permanently
            # non-definitive: ``check_spf_status`` never caches an "error", so
            # every outbound message would re-walk the chain over a name that
            # can never resolve. ``arc_dns_txt`` draws the same line.
            logger.debug("Unformable include name %s", include_domain)
            continue
        except ResolverError as exc:
            # Timeout, SERVFAIL, a bogus DNSSEC chain: we did not learn what
            # this include publishes, which is not the same as learning it
            # publishes nothing. Left unexplored so the caller reports an
            # error instead of blaming the found record.
            logger.debug(
                "DNS resolution failed for %s (%s), may retry", include_domain, exc
            )
            transient.add(include_domain)
            continue
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # Our own bug, not an answer from DNS. Marked transient for the
            # same reason: leaving it out of both sets makes the domain read
            # as looked-up-and-settled, so the caller returns a definitive
            # "incorrect" that gets cached for ten minutes and marks every
            # external recipient for retry — over a fault of ours.
            logger.warning(
                "Unexpected error resolving %s: %s", include_domain, exc, exc_info=True
            )
            capture_exception(exc)
            transient.add(include_domain)
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
        for child_domain in spf_delegated_domains(spf_records[0]):
            if child_domain not in visited:
                queue.append(child_domain)

    return resolved, visited, transient, error


def _resolve_dns_values(record_type, query_name):
    """Resolve DNS and return found values and normalized expected value flag."""
    if record_type.upper() == "MX":
        answer = resolve_answer(query_name, "MX")
        return [f"{rr.preference} {rr.exchange}" for rr in answer.rrset]

    if record_type.upper() == "TXT":
        # One value per record, with each record's character-strings joined
        # and no separator, per RFC 6376 §3.6.2.2 (DKIM) and RFC 7208 §3.3
        # (SPF). Querying authoritative servers directly, an RR's several
        # strings are always one value: only a local stub resolver such as
        # systemd-resolved merges distinct TXT records into one RR.
        return [
            normalize_txt_value(v)
            for v in resolve_answer(query_name, "TXT").text_values()
        ]

    answer = resolve_answer(query_name, record_type)
    return [rr.to_text() for rr in answer.rrset]


def _check_txt_security(expected_value, found_values):
    """Check for duplicate/insecure SPF and DMARC records. Returns result or None."""
    # SPF duplicate check. Whether the policy is strong enough is left to
    # _check_spf: a weak "all" only reads as "insecure" once the delegation
    # is known to be in place, and "insecure" is a status we still send on.
    if is_spf_record(expected_value):
        if len([v for v in found_values if is_spf_record(v)]) > 1:
            return {"status": "duplicate", "found": found_values}

    # DMARC duplicate and insecure checks. Both sides go through the RFC 7489
    # tag parser rather than a substring search: "p=none" also occurs inside
    # "sp=none", which governs subdomains only, so a substring match reads a
    # strict domain policy as insecure and — on the expected side — switches
    # the whole check off whenever the operator configures an "sp".
    if is_dmarc_record(expected_value):
        dmarc_records = [v for v in found_values if is_dmarc_record(v)]
        if len(dmarc_records) > 1:
            return {"status": "duplicate", "found": found_values}
        # Syntax before strength. A record receivers cannot parse is ignored
        # outright (RFC 7489 6.6.3), so it protects nothing — reporting it as
        # "insecure" would read as "works, but weaker than we asked" and send
        # the operator looking at their policy instead of their typo.
        for dmarc in dmarc_records:
            if not dmarc_syntax_is_valid(dmarc):
                return {"status": "incorrect", "found": found_values}
        expected_policy = dmarc_policy(expected_value)
        for dmarc in dmarc_records:
            if not _dmarc_policy_is_acceptable(expected_policy, dmarc_policy(dmarc)):
                return {"status": "insecure", "found": found_values}

    return None


def _dmarc_policy_is_acceptable(expected_policy: str, found_policy: str) -> bool:
    """Whether a found DMARC policy is at least as strict as expected.

    The same shape as :func:`_all_is_acceptable` for SPF: a domain that went
    further than we asked (we expect "quarantine", they publish "reject") is
    correct, not a mismatch.
    """
    return (
        DMARC_POLICY_STRICTNESS[found_policy]
        >= DMARC_POLICY_STRICTNESS[expected_policy]
    )


def check_single_record(
    maildomain: MailDomain,
    expected_record: Dict[str, any],
    deadline: Optional[float] = None,
) -> Dict[str, any]:
    """
    Check a single DNS record for a mail domain.

    Args:
        maildomain: The MailDomain instance
        expected_record: The expected record to check
        deadline: ``time.monotonic()`` value bounding the whole check. Past it
            the record is reported as "error" without a lookup, which is the
            same not-definitive status a timeout gets — never cached, never a
            verdict on the customer's DNS.

    Returns:
        Check result dictionary with status and details
    """
    record_type = expected_record["type"]
    target = expected_record["target"]
    expected_value = expected_record["value"]

    # Build the query name
    query_name = f"{target}.{maildomain.name}" if target else maildomain.name

    if deadline is not None and time.monotonic() >= deadline:
        return {
            "status": "error",
            "error": "DNS check budget exhausted before this record was checked",
        }

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
            return _check_spf(expected_value, found_values, deadline=deadline)

        # Exact match (non-SPF)
        if expected_value in found_values:
            return {"status": "correct", "found": found_values}

        # Semantic fallback for DKIM
        if record_type.upper() == "TXT" and target.endswith("._domainkey"):
            result = _check_dkim_semantic(expected_value, found_values)
            if result:
                return result

        # Semantic fallback for DMARC, so a record is judged on what it asks
        # receivers to do rather than on matching our spelling of it.
        if record_type.upper() == "TXT" and is_dmarc_record(expected_value):
            result = _check_dmarc_semantic(expected_value, found_values)
            if result:
                return result

        return {"status": "incorrect", "found": found_values}

    except NXDOMAINError:
        return {"status": "missing", "error": "Domain not found"}
    except NoAnswerError:
        return {"status": "missing", "error": "No records found"}
    except ServfailError:
        # "error", not "missing": a SERVFAIL says the lookup did not complete,
        # not that the record is absent. ``check_spf_status`` caches anything
        # other than "error" for 10 minutes, and a definitive-looking negative
        # there marks every external recipient for retry — so one bad answer
        # from an authoritative server would park the domain's outbound mail.
        # ``_resolve_spf_includes`` classifies the same failure as transient.
        return {"status": "error", "error": "DNS query failed (SERVFAIL)"}
    except ResolutionTimeoutError:
        return {"status": "error", "error": "DNS query timeout"}
    except InvalidNameError:
        return {"status": "error", "error": "Invalid domain name"}
    except DNSSECError as e:
        # "error", not "missing": the record may well be published, but the
        # zone's signatures do not verify, so we cannot report on what is in
        # it. Telling the operator their record is absent would send them
        # editing DNS instead of looking at their DNSSEC.
        return {"status": "error", "error": f"DNSSEC validation failed: {e}"}
    except ResolverError as e:
        return {"status": "error", "error": f"DNS query failed: {e}"}
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Every DNS failure we know how to read is handled above, so reaching
        # here means a bug rather than a bad zone. Reported, not just folded
        # into a status string the operator reads as "DNS is flaky".
        logger.warning(
            "Unexpected error checking %s for %s: %s",
            record_type,
            query_name,
            e,
            exc_info=True,
        )
        capture_exception(e)
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

    # Bounded here too: this runs on the send path, where an unbounded chain
    # walk holds a worker instead of a request, and the result is cached for
    # ten minutes so it is not worth minutes to compute.
    deadline = time.monotonic() + DNS_CHECK_TOTAL_TIMEOUT
    for expected_record in spf_records:
        result = check_single_record(maildomain, expected_record, deadline=deadline)
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

    # One budget for the whole check, shared by every record: each resolution
    # is bounded on its own, but this endpoint is synchronous and their sum is
    # not. Records the budget does not reach report "error", which reads as
    # "could not check" rather than "your DNS is wrong".
    deadline = time.monotonic() + DNS_CHECK_TOTAL_TIMEOUT

    for expected_record in expected_records:
        result_record = expected_record.copy()
        result_record["_check"] = check_single_record(
            maildomain, expected_record, deadline=deadline
        )

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
