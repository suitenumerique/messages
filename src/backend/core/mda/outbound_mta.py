"""
Outbound MTA (Mail Transfer Agent) functionality for sending emails via MX records.

This module handles the resolution of MX records for recipient domains and
routes outbound messages through appropriate mail servers using direct SMTP connections.
"""

import logging
import random
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

import dns.resolver

from core import models
from core.enums import MessageDeliveryStatusChoices
from core.mda.smtp import send_smtp_mail

logger = logging.getLogger(__name__)


def resolve_mx_records(domain: str) -> List[Tuple[int, str]]:
    """
    Resolve MX records for a domain, returning a list of (priority, hostname) tuples, sorted by priority.
    Falls back to A record if no MX is found.
    """
    try:
        answers = dns.resolver.resolve(domain, "MX")
        mx_records = sorted(
            [(r.preference, str(r.exchange).rstrip(".")) for r in answers],
            key=lambda x: x[0],
        )
        if mx_records:
            return mx_records
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
        logger.warning("No MX records for %s, falling back to A record.", domain)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error resolving MX for %s: %s", domain, e)
        return []
    # Fallback to A record
    try:
        answers = dns.resolver.resolve(domain, "A")
        return [(0, str(r)) for r in answers]
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error resolving A record for %s: %s", domain, e)
        return []


def resolve_hostname_ip(hostname: str) -> Optional[str]:
    """
    Resolve a hostname to its first A record IP address, with a direct DNS query
    """
    try:
        answers = dns.resolver.resolve(hostname, "A")
        for r in answers:
            return str(r)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error resolving IP for %s: %s", hostname, e)
    return None


def group_recipients_by_mx(recipients: List[str]) -> Dict[str, List[str]]:
    """
    Group recipient emails by their MX hostname (not IP).
    Returns a dict: {mx_hostname: [recipient_emails]}
    """
    mx_map = defaultdict(list)
    for email in recipients:
        domain = email.split("@")[-1]
        mx_records = resolve_mx_records(domain)
        if mx_records:
            # Use the lowest-priority MX
            # TODO: In case of failure, we should try the next MX
            _, mx_hostname = mx_records[0]
            mx_map[mx_hostname].append(email)
        else:
            logger.error("No MX or A record found for domain %s", domain)
    return mx_map


def select_smtp_proxy() -> Dict[str, Any]:
    """
    Select an SMTP proxy to use for sending messages.
    """
    if len(settings.MTA_OUT_PROXIES) > 0:
        proxy = random.choice(settings.MTA_OUT_PROXIES)  # noqa: S311
        parsed = urlparse(proxy)
        return {
            "proxy_host": parsed.hostname,
            "proxy_port": parsed.port,
            "proxy_username": parsed.username,
            "proxy_password": parsed.password,
            "proxy_tls": parsed.scheme == "socks5s",
            "sender_hostname": parsed.hostname,
        }
    return {}


def send_message_via_mx(message: models.Message) -> Dict[str, Any]:
    """
    Send a message to external recipients by resolving MX and delivering via SMTP.
    Groups by MX hostname, resolves IP, and uses send_smtp_mail for delivery.
    Returns a dict of recipient statuses.
    """
    mime_data = message.blob.get_content()
    # Get all recipients that need delivery
    envelope_to = {
        recipient.contact.email: recipient
        for recipient in message.recipients.select_related("contact").all()
        if recipient.delivery_status in {None, MessageDeliveryStatusChoices.RETRY}
        and (recipient.retry_at is None or recipient.retry_at <= timezone.now())
    }
    statuses = {}
    mx_groups = group_recipients_by_mx(list(envelope_to.keys()))
    for mx_hostname, recipient_emails in mx_groups.items():
        mx_ip = resolve_hostname_ip(mx_hostname)
        if not mx_ip:
            logger.error("Could not resolve IP for MX %s", mx_hostname)
            for email in recipient_emails:
                statuses[email] = {
                    "error": f"Could not resolve MX IP for {mx_hostname}",
                    "delivered": False,
                }
            continue
        logger.info(
            "Sending to MX %s (%s) for recipients: %s",
            mx_hostname,
            mx_ip,
            recipient_emails,
        )

        proxy_settings = select_smtp_proxy()

        # Use direct SMTP, no auth
        smtp_statuses = send_smtp_mail(
            smtp_host=mx_ip,
            smtp_port=settings.MTA_OUT_DIRECT_PORT,
            envelope_from=message.sender.email,
            recipient_emails=recipient_emails,
            message_content=mime_data,
            **proxy_settings,
        )
        statuses.update(smtp_statuses)
    return statuses
