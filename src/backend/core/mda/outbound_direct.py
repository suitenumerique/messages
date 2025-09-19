"""
Outbound MTA (Mail Transfer Agent) functionality for sending emails via MX records.

This module handles the resolution of MX records for recipient domains and
routes outbound messages through appropriate mail servers using direct SMTP connections.
"""

import logging
import random
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from django.conf import settings

import dns.resolver

from core.mda.smtp import send_smtp_mail

logger = logging.getLogger(__name__)


def resolve_mx_records(domain: str) -> List[Tuple[int, str]]:
    """
    Resolve MX records for a domain, returning a list of (priority, hostname) tuples, sorted by priority.
    Falls back to A record if no MX is found.
    """
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=10)
        mx_records = sorted(
            [(r.preference, str(r.exchange).rstrip(".")) for r in answers],
            key=lambda x: x[0],
        )
        if mx_records:
            return mx_records
    except dns.resolver.NoAnswer:
        logger.warning("No MX records for %s, falling back to A record.", domain)

        # Fallback to A record
        return [(10, domain)]
    except dns.resolver.NoNameservers:
        logger.warning("Domain %s has no nameservers", domain)
    except (dns.resolver.NXDOMAIN, dns.resolver.YXDOMAIN):
        logger.warning("Domain %s does not exist or is too long", domain)
    except dns.resolver.LifetimeTimeout:
        logger.warning("DNS resolution timeout for %s", domain)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error resolving MX for %s: %s", domain, e)

    # This will trigger a retry for all recipients
    return []


def resolve_hostname_ip(hostname: str) -> Optional[str]:
    """
    Resolve a hostname to its first A record IP address, with a direct DNS query
    """
    try:
        answers = dns.resolver.resolve(hostname, "A", lifetime=10)
        for r in answers:
            return str(r)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error resolving IP for %s: %s", hostname, e)
    return None


def group_recipients_by_mx(recipients: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Group recipient emails by their domain, returning all MX records for each domain.
    Returns a dict: {domain: {"mx_records": [(priority, mx_hostname)], "recipients": [emails]}}
    """
    domain_map = {}
    for email in recipients:
        # Validate email format and extract domain
        parts = email.split("@")
        if len(parts) != 2 or not parts[1].strip():
            logger.error("Invalid email format while MX grouping: %s", email)
            continue
        domain = parts[1].lower().strip()
        if domain not in domain_map:
            domain_map[domain] = {
                "mx_records": resolve_mx_records(domain),
                "recipients": set(),
            }
        domain_map[domain]["recipients"].add(email)
    return domain_map


def select_smtp_proxy() -> Dict[str, Any]:
    """
    Select an SMTP proxy to use for sending messages.
    """
    if len(settings.MTA_OUT_DIRECT_PROXIES) > 0:
        proxy = random.choice(settings.MTA_OUT_DIRECT_PROXIES)  # noqa: S311
        parsed = urlparse(proxy)
        return {
            "proxy_host": parsed.hostname,
            "proxy_port": parsed.port,
            "proxy_username": parsed.username,
            "proxy_password": parsed.password,
            "sender_hostname": parsed.hostname,
        }
    return {}


def send_message_via_mx(envelope_from, recipient_emails, mime_data) -> Dict[str, Any]:
    """
    Send a message to external recipients by resolving MX and delivering via SMTP.
    Implements MX fallback logic: tries each MX in priority order, retrying failed
    addresses on subsequent MX servers until all succeed or permanently fail.
    Returns a dict of recipient statuses.
    """

    final_statuses = {}
    domain_groups = group_recipients_by_mx(recipient_emails)

    for domain, domain_info in domain_groups.items():
        mx_records = domain_info["mx_records"]

        logger.info(
            "Processing domain %s with %d MX records for %d recipients",
            domain,
            len(mx_records),
            len(domain_info["recipients"]),
        )

        # Track which recipients still need delivery for this domain
        remaining_recipients = domain_info["recipients"].copy()
        smtp_statuses = None

        # Try each MX record in priority order
        for priority, mx_hostname in mx_records:
            if not remaining_recipients:
                logger.info("All recipients for domain %s have been delivered", domain)
                break

            mx_ip = resolve_hostname_ip(mx_hostname)
            if not mx_ip:
                logger.error(
                    "Could not resolve IP for MX %s (priority %d)",
                    mx_hostname,
                    priority,
                )
                continue

            logger.info(
                "Trying MX %s (%s) priority %d for domain %s, remaining recipients: %s",
                mx_hostname,
                mx_ip,
                priority,
                domain,
                remaining_recipients,
            )

            proxy_settings = select_smtp_proxy()

            # Use direct SMTP, no auth
            smtp_statuses = send_smtp_mail(
                smtp_host=mx_hostname,
                smtp_ip=mx_ip,
                smtp_port=settings.MTA_OUT_DIRECT_PORT,
                envelope_from=envelope_from,
                recipient_emails=remaining_recipients.copy(),
                message_content=mime_data,
                **proxy_settings,
            )

            # Process results and update remaining recipients
            new_remaining = set()
            for email, status in smtp_statuses.items():
                if status.get("delivered", False):
                    # Success - add to final statuses
                    final_statuses[email] = status
                    remaining_recipients.discard(email)
                    logger.info(
                        "Successfully delivered to %s via MX %s", email, mx_hostname
                    )
                elif status.get("retry", False):
                    # Retry on next MX
                    new_remaining.add(email)
                    logger.info(
                        "Will retry %s on next MX (current: %s)", email, mx_hostname
                    )
                else:
                    # Permanent failure
                    final_statuses[email] = status
                    remaining_recipients.discard(email)
                    logger.warning(
                        "Permanent failure for %s via MX %s: %s",
                        email,
                        mx_hostname,
                        status.get("error", "Unknown error"),
                    )

            # Update remaining recipients for next iteration
            remaining_recipients = new_remaining

        # If this was the last MX and we still have remaining recipients, preserve their last error
        if remaining_recipients:
            logger.error(
                "All MX records exhausted for domain %s, preserving last error for remaining recipients: %s",
                domain,
                remaining_recipients,
            )
            if smtp_statuses is None:
                for email in remaining_recipients:
                    final_statuses[email] = {
                        "delivered": False,
                        "error": f"No available MX records for {domain}",
                        "retry": True,
                    }
            else:
                for email in remaining_recipients:
                    # Find the last error status + retry flag for this recipient from the most recent SMTP call
                    final_statuses[email] = smtp_statuses[email]

    return final_statuses
