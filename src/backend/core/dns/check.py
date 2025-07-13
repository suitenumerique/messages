"""
DNS checking functionality for mail domains.
"""

from typing import Dict, List

import dns.resolver

from core.models import MailDomain


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
    if target:
        query_name = f"{target}.{maildomain.name}"
    else:
        query_name = maildomain.name

    try:
        # Query DNS records
        if record_type.upper() == "MX":
            answers = dns.resolver.resolve(query_name, "MX")
            found_values = [
                f"{answer.preference} {answer.exchange}" for answer in answers
            ]
        elif record_type.upper() == "TXT":
            answers = dns.resolver.resolve(query_name, "TXT")
            found_values = [answer.to_text().strip('"') for answer in answers]
        else:
            # For other record types, try to resolve them as-is
            answers = dns.resolver.resolve(query_name, record_type)
            found_values = [answer.to_text() for answer in answers]

        # Check if expected value is in found values
        if expected_value in found_values:
            return {"status": "correct", "found": found_values}

        return {"status": "incorrect", "found": found_values}

    except dns.resolver.NXDOMAIN:
        # Domain doesn't exist
        return {"status": "missing", "error": "Domain not found"}
    except dns.resolver.NoAnswer:
        # No records found for this query
        return {"status": "missing", "error": "No records found"}
    except dns.resolver.NoNameservers:
        # No nameservers found
        return {"status": "missing", "error": "No nameservers found"}
    except dns.resolver.Timeout:
        # DNS query timed out
        return {"status": "error", "error": "DNS query timeout"}
    except dns.resolver.YXDOMAIN:
        # Domain name is too long
        return {"status": "error", "error": "Domain name too long"}
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Other DNS errors
        return {"status": "error", "error": f"DNS query failed: {str(e)}"}


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

    for expected_record in expected_records:
        result_record = expected_record.copy()
        result_record["_check"] = check_single_record(maildomain, expected_record)
        results.append(result_record)

    return results
