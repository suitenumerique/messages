"""
DNS checking functionality for mail domains.
"""

import collections
import logging
import re
from typing import Dict, List, Optional, Tuple

from django.core.cache import cache

import dns.resolver

from core.models import MailDomain

logger = logging.getLogger(__name__)

SPF_CHECK_CACHE_KEY_PREFIX = "dns:spf_check:"
SPF_CHECK_CACHE_TIMEOUT = 600  # 10 minutes


def normalize_txt_value(value: str) -> str:
    """
    Normalize a TXT record value.
    """
    return re.sub(r"\;$", "", re.sub(r"\s*\;\s*", ";", value.strip('"')))


def parse_dkim_tags(value: str) -> Optional[Dict[str, str]]:
    """Parse a DKIM record into a dict of tag=value pairs.

    Per RFC 6376, tags are separated by semicolons, with tag=value format.
    The v= tag MUST be first and equal to DKIM1.
    Returns None if the record is not a valid DKIM record.
    """
    parts = [p.strip() for p in value.split(";") if p.strip()]
    if not parts:
        return None
    # v= must be first
    first = parts[0]
    if not first.startswith("v=") or first.split("=", 1)[1].strip() != "DKIM1":
        return None
    tags = {}
    for part in parts:
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        tags[key.strip()] = val.strip()
    return tags


def parse_spf_terms(value: str) -> Optional[Tuple[str, set]]:
    """Parse an SPF record into its qualifier-all and set of other terms.

    Per RFC 7208, v=spf1 must be first. Returns (all_mechanism, other_terms)
    where all_mechanism is e.g. "-all", "~all", "+all", "?all" or None,
    and other_terms is the set of remaining mechanisms/modifiers.
    Returns None if not a valid SPF record.
    """
    if not value.startswith("v=spf1"):
        return None
    rest = value[len("v=spf1") :].strip()
    terms = rest.split()
    all_mechanism = None
    other_terms = set()
    for term in terms:
        if term in ("-all", "~all", "+all", "?all", "all"):
            all_mechanism = term
        else:
            other_terms.add(term)
    return (all_mechanism, other_terms)


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
        if not all(found_tags.get(k) == v for k, v in expected_tags.items()):
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
    found_spf_values = [v for v in found_values if parse_spf_terms(v)]
    if not found_spf_values:
        return {"status": "missing", "found": found_values}

    # If there are expected includes, check they resolve via BFS.
    # This is the primary signal: includes being set up is what matters.
    if expected_includes:
        resolved, error = _resolve_spf_includes(found_values)
        if error and error.startswith("duplicate:"):
            return {"status": "duplicate", "found": found_values}
        if error == "limit_reached":
            return {"status": "incorrect", "found": found_values}
        if not expected_includes <= resolved:
            return {"status": "incorrect", "found": found_values}
        # Includes resolve — check if "all" mechanism is acceptable
        if _found_all_matches(expected_all, found_spf_values):
            return {"status": "correct", "found": found_values}
        return {"status": "insecure", "found": found_values}

    # No includes: direct terms comparison (order-independent, ~all accepted for -all)
    for found_value in found_spf_values:
        found_all, found_terms = parse_spf_terms(found_value)
        all_ok = expected_all == found_all or (
            expected_all == "-all" and found_all == "~all"
        )
        if expected_terms <= found_terms:
            if all_ok:
                return {"status": "correct", "found": found_values}
            return {"status": "insecure", "found": found_values}

    return {"status": "incorrect", "found": found_values}


def _found_all_matches(expected_all: str, found_values: List[str]) -> bool:
    """Check if any found SPF record has an acceptable "all" mechanism."""
    for found_value in found_values:
        found = parse_spf_terms(found_value)
        if not found:
            continue
        found_all, _ = found
        if expected_all == found_all or (
            expected_all == "-all" and found_all == "~all"
        ):
            return True
    return False


def _extract_include_domains(spf_value: str) -> List[str]:
    """Extract include: domains from an SPF value, preserving order."""
    return [
        term[len("include:") :]
        for term in spf_value.split()
        if term.startswith("include:")
    ]


def _resolve_spf_includes(
    found_values: List[str], max_lookups: int = 10
) -> Tuple[set, Optional[str]]:
    """BFS through SPF include chains, return all domains with valid SPF records.

    Seeds from include: domains in found_values, follows the chain via BFS.
    Per RFC 7208, stops after max_lookups DNS lookups.

    Returns:
        (resolved_domains, error) where error is None on success, or a string
        describing the issue ("limit_reached", "duplicate:domain.com").
    """
    queue = collections.deque()
    for found_value in found_values:
        if found_value.startswith("v=spf1"):
            queue.extend(_extract_include_domains(found_value))

    visited = set()
    resolved = set()
    lookup_count = 0

    while queue:
        if lookup_count >= max_lookups:
            return resolved, "limit_reached"

        include_domain = queue.popleft()
        if include_domain in visited:
            continue
        visited.add(include_domain)
        lookup_count += 1

        try:
            answers = dns.resolver.resolve(include_domain, "TXT")
            spf_records = []
            for rr in answers.rrset:
                for s in rr.strings:
                    txt_value = normalize_txt_value(s.decode())
                    if txt_value.startswith("v=spf1"):
                        spf_records.append(txt_value)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("DNS resolution failed for %s", include_domain)
            continue

        if len(spf_records) > 1:
            return resolved, f"duplicate:{include_domain}"

        if not spf_records:
            continue

        resolved.add(include_domain)
        for child_domain in _extract_include_domains(spf_records[0]):
            if child_domain not in visited:
                queue.append(child_domain)

    return resolved, None


def _resolve_dns_values(record_type, target, query_name):
    """Resolve DNS and return found values and normalized expected value flag."""
    if record_type.upper() == "MX":
        answers = dns.resolver.resolve(query_name, "MX")
        return [f"{answer.preference} {answer.exchange}" for answer in answers]

    if record_type.upper() == "TXT":
        answers = dns.resolver.resolve(query_name, "TXT")
        # Some local resolvers (e.g. systemd-resolved) merge separate TXT
        # records into a single RR with multiple strings. DKIM keys can also
        # legitimately span multiple strings within one record. We handle
        # both by emitting each individual string as a value, plus the
        # concatenated form for DKIM.
        values = []
        for rr in answers.rrset:
            if target.endswith("._domainkey"):
                # DKIM: concatenate strings (long key split across strings)
                values.append(normalize_txt_value(b"".join(rr.strings).decode()))
            else:
                # Other TXT: treat each string as a separate value
                for s in rr.strings:
                    values.append(normalize_txt_value(s.decode()))
        return values

    answers = dns.resolver.resolve(query_name, record_type)
    return [answer.to_text() for answer in answers]


def _check_txt_security(expected_value, found_values):
    """Check for duplicate/insecure SPF and DMARC records. Returns result or None."""
    # SPF duplicate and insecure checks
    if expected_value.startswith("v=spf1"):
        spf_records = [v for v in found_values if v.startswith("v=spf1")]
        if len(spf_records) > 1:
            return {"status": "duplicate", "found": found_values}
        if expected_value.endswith("-all"):
            for spf in spf_records:
                if spf.endswith("+all") or spf.endswith("?all"):
                    return {"status": "insecure", "found": found_values}

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
        found_values = _resolve_dns_values(record_type, target, query_name)
        if record_type.upper() == "TXT":
            expected_value = normalize_txt_value(expected_value)

        # Check for duplicate/insecure SPF and DMARC
        if record_type.upper() == "TXT":
            security_result = _check_txt_security(expected_value, found_values)
            if security_result:
                return security_result

        # SPF: always use semantic check (handles exact match, reordering,
        # ~all acceptance, and recursive include verification)
        if record_type.upper() == "TXT" and expected_value.startswith("v=spf1"):
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
        if r["type"].upper() == "TXT" and r["value"].startswith("v=spf1")
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
