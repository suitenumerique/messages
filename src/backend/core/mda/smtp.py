"""Standalone SMTP client for sending emails, with support for SOCKS5 proxies."""

import logging
import smtplib
import ssl
from typing import Any, Dict, Optional

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
    recipient_emails: set[str],
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
        recipient_emails: Set of recipient email addresses
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
        Dict mapping recipient emails to delivery status with retry flag:
        {
            "recipient@example.com": {
                "delivered": bool,
                "error": str (if not delivered),
                "retry": bool (whether to retry if not delivered)
            }
        }
    """
    statuses = {}

    def error_for_all_recipients(error: str, retry: bool) -> Dict[str, Any]:
        return {
            email: {"delivered": False, "error": error, "retry": retry}
            for email in recipient_emails
        }

    client = ProxySMTP(
        host=None,
        port=None,
        timeout=timeout,
        proxy_host=proxy_host,
        proxy_port=proxy_port,
        proxy_username=proxy_username,
        proxy_password=proxy_password,
        local_hostname=sender_hostname,
    )

    def _quit():
        """Close the connection, sending a QUIT command to be polite but ignoring any errors"""
        try:
            client.quit()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("SMTP: QUIT failed %s", e)

    try:
        (code, msg) = client.connect(smtp_host, smtp_port)
        if code != 220:
            _quit()
            return error_for_all_recipients(f"Connection failed: {code} {msg}", True)

        logger.debug("SMTP: connected to %s:%s (%s)", smtp_host, smtp_port, msg)

        (code, msg) = client.ehlo(sender_hostname)
        logger.debug("SMTP: EHLO response: %s %s", code, msg)

        if not 200 <= code <= 299:
            (code, msg) = client.helo(sender_hostname)
            logger.debug("SMTP: HELO response: %s %s", code, msg)
            if not 200 <= code <= 299:
                _quit()
                return error_for_all_recipients(f"HELO failed: {code} {msg}", True)

        if client.has_extn("starttls"):
            # smtplib.SMTP.starttls() doesn't validate certificates by default!
            # https://github.com/python/cpython/issues/91826
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
            (code, msg) = client.starttls(context=ssl_context)
            logger.debug("SMTP: STARTTLS response: %s %s", code, msg)
            if not 200 <= code <= 299:
                _quit()
                return error_for_all_recipients(f"STARTTLS failed: {code} {msg}", True)

            # Restart the SMTP session now that we're in TLS mode
            (code, msg) = client.ehlo(sender_hostname)
            logger.debug("SMTP: EHLO2 response: %s %s", code, msg)
            if not 200 <= code <= 299:
                _quit()
                return error_for_all_recipients(
                    f"EHLO after STARTTLS failed: {code} {msg}", True
                )

        if smtp_username and smtp_password:
            try:
                client.login(smtp_username, smtp_password)
            except smtplib.SMTPAuthenticationError as auth_err:
                _quit()
                logger.error(
                    "SMTP auth failed for user '%s': %s",
                    smtp_username,
                    auth_err,
                    exc_info=True,
                )
                return error_for_all_recipients("SMTP auth failed", True)

    except Exception as e:  # pylint: disable=broad-exception-caught
        _quit()
        return error_for_all_recipients(str(e), True)

    # At this stage, we now have a connected, valid SMTP session.
    # Start trying to deliver the message.

    try:
        recipient_errors = client.sendmail(
            envelope_from, recipient_emails, message_content
        )
    except smtplib.SMTPSenderRefused as e:
        return error_for_all_recipients(
            f"Sender refused: {e.smtp_code} {e.smtp_error}", 400 <= e.smtp_code <= 499
        )
    except smtplib.SMTPDataError as e:
        return error_for_all_recipients(
            f"Data error: {e.smtp_code} {e.smtp_error}", 400 <= e.smtp_code <= 499
        )
    except smtplib.SMTPRecipientsRefused as e:
        _quit()
        for recipient, code_msg in e.recipients.items():
            statuses[recipient] = {
                "delivered": False,
                "error": f"Recipient refused: {code_msg[0]} {code_msg[1]}",  # (code, msg)
                "retry": 400 <= code_msg[0] <= 499,
            }
        return statuses
    except Exception as e:  # pylint: disable=broad-exception-caught
        _quit()
        return error_for_all_recipients(str(e), True)

    _quit()

    logger.info(
        "Sent message via SMTP to %s. Response: %s",
        recipient_emails,
        recipient_errors,
    )

    for recipient_email in recipient_emails:
        if recipient_email not in recipient_errors:
            statuses[recipient_email] = {"delivered": True}
        else:
            code_msg = recipient_errors[recipient_email]
            statuses[recipient_email] = {
                "delivered": False,
                "error": f"Recipient refused: {code_msg[0]} {code_msg[1]}",  # (code, msg)
                "retry": 400 <= code_msg[0] <= 499,
            }

    return statuses
