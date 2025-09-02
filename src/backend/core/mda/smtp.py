"""Standalone SMTP client for sending emails, with support for SOCKS5 proxies."""

import logging
import smtplib
from typing import Any, Dict, List, Optional

import socks

logger = logging.getLogger(__name__)


def create_proxied_socket(
    proxy_host,
    proxy_port,
    target_host,
    target_port,
    username=None,
    password=None,
    timeout=None,
):
    """Create a socket connected through a SOCKS proxy"""
    proxy = socks.socksocket()
    if type(timeout) in {int, float}:
        proxy.settimeout(timeout)
    proxy.set_proxy(
        socks.PROXY_TYPE_SOCKS5,
        proxy_host,
        proxy_port,
        rdns=False,  # we are fine with local hostname resolution
        username=username,
        password=password,
    )
    proxy.connect((target_host, target_port))

    return proxy


class ProxySMTP(smtplib.SMTP):
    """SMTP client that connects through a SOCKS5 proxy with support for nested SSL."""

    def __init__(self, host, port, *args, **kwargs):
        self.proxy_host = kwargs.pop("proxy_host", None)
        self.proxy_port = kwargs.pop("proxy_port", None)
        self.proxy_username = kwargs.pop("proxy_username", None)
        self.proxy_password = kwargs.pop("proxy_password", None)

        super().__init__(host, port, *args, **kwargs)

    def _get_socket(self, host, port, timeout):
        """
        Get a socket connection, either direct or through SOCKS5 proxy.

        Args:
            host: Target SMTP hostname
            port: Target SMTP port
            timeout: Connection timeout

        Returns:
            Socket connection to the target host
        """
        if self.proxy_host is None:
            return super()._get_socket(host, port, timeout)

        if timeout is not None and not timeout:
            raise ValueError("Non-blocking socket (timeout=0) is not supported")
        if self.debuglevel > 0:
            self._print_debug("connect: to", (host, port), self.source_address)

        return create_proxied_socket(
            self.proxy_host,
            self.proxy_port,
            host,
            port,
            self.proxy_username,
            self.proxy_password,
            timeout,
        )


# pylint: disable=too-many-arguments
def send_smtp_mail(
    smtp_host: str,
    smtp_port: int,
    envelope_from: str,
    recipient_emails: List[str],
    message_content: bytes,
    smtp_username: Optional[str] = None,
    smtp_password: Optional[str] = None,
    timeout: int = 60,
    proxy_host: Optional[str] = None,
    proxy_port: Optional[int] = None,
    proxy_username: Optional[str] = None,
    proxy_password: Optional[str] = None,
    sender_hostname: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Send an email via SMTP.

    Args:
        smtp_host: SMTP server hostname
        smtp_port: SMTP server port
        envelope_from: Sender email address
        recipient_emails: List of recipient email addresses
        message_content: Raw email message (bytes)
        smtp_username: SMTP username (optional)
        smtp_password: SMTP password (optional)
        timeout: Connection timeout in seconds
        proxy_host: SOCKS5 proxy hostname
        proxy_port: SOCKS5 proxy port
        proxy_username: SOCKS5 proxy username
        proxy_password: SOCKS5 proxy password
        sender_hostname: Local hostname to use for SMTP EHLO/HELO

    Returns:
        Dict mapping recipient emails to delivery status or error
    """
    statuses = {}
    try:
        with ProxySMTP(
            host=None,
            port=None,
            timeout=timeout,
            proxy_host=proxy_host,
            proxy_port=proxy_port,
            proxy_username=proxy_username,
            proxy_password=proxy_password,
            local_hostname=sender_hostname,
        ) as client:
            (code, msg) = client.connect(smtp_host, smtp_port)
            if code != 220:
                client.close()
                raise smtplib.SMTPConnectError(code, str(msg))

            logger.debug("SMTP: connected to %s:%s (%s)", smtp_host, smtp_port, msg)

            (code, msg) = client.ehlo(sender_hostname)

            logger.debug("SMTP: EHLO response: %s %s", code, msg)

            if not 200 <= code <= 299:
                (code, msg) = client.helo(sender_hostname)
                logger.debug("SMTP: HELO response: %s %s", code, msg)
                if not 200 <= code <= 299:
                    client.close()
                    raise smtplib.SMTPHeloError(code, str(msg))

            if client.has_extn("starttls"):
                (code, msg) = client.starttls()
                logger.debug("SMTP: STARTTLS response: %s %s", code, msg)
                if not 200 <= code <= 299:
                    client.close()
                    raise smtplib.SMTPNotSupportedError(code, str(msg))

                # Restart the SMTP session now that we're in TLS mode
                (code, msg) = client.ehlo(sender_hostname)
                logger.debug("SMTP: EHLO2 response: %s %s", code, msg)
                if not 200 <= code <= 299:
                    client.close()
                    raise smtplib.SMTPHeloError(code, str(msg))

            if smtp_username and smtp_password:
                try:
                    client.login(smtp_username, smtp_password)
                except smtplib.SMTPAuthenticationError as auth_err:
                    logger.error("SMTP auth failed for user '%s': %s", smtp_username, auth_err, exc_info=True)
                    for email in recipient_emails:
                        statuses[email] = {"error": f"auth_failed: {auth_err}", "delivered": False}
                    return statuses

            smtp_response = client.sendmail(
                envelope_from, recipient_emails, message_content
            )
            logger.info(
                "Sent message via SMTP to %s. Response: %s",
                recipient_emails,
                smtp_response,
            )
            for recipient_email in recipient_emails:
                if recipient_email not in smtp_response:
                    statuses[recipient_email] = {"delivered": True}
                else:
                    statuses[recipient_email] = {
                        "error": smtp_response[recipient_email],
                        "delivered": False,
                    }
    except (smtplib.SMTPException, OSError) as e:
        logger.error("SMTP error sending message: %s", e, exc_info=True)
        for email in recipient_emails:
            statuses[email] = {"error": str(e), "delivered": False}
    return statuses
