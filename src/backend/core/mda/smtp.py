"""Standalone SMTP client for sending emails, with support for SOCKS5 proxies."""

import logging
import smtplib
import ssl
from dataclasses import dataclass
from typing import Any, Dict, Optional

import socks


@dataclass(frozen=True)
class SmtpProxy:
    """SOCKS5 proxy + EHLO identity to use when sending."""

    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    sender_hostname: Optional[str] = None


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


def _build_tls_context(level: str) -> ssl.SSLContext:
    """Build an SSL context matching Postfix's smtp_tls_security_level semantics.

    "secure" performs full PKI verification (CA chain + hostname). "may" creates
    an unverified context: many public MXes serve mismatched or self-signed
    certs, and rejecting them would just push delivery to cleartext anyway.
    """
    ctx = ssl.create_default_context()
    if level != "secure":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _starttls_upgrade(
    client: "ProxySMTP", level: str, sender_hostname: Optional[str]
) -> Optional[str]:
    """Run the STARTTLS dance, with policy-aware fallback.

    Returns None on success (or when policy allows continuing in cleartext),
    the sentinel ``"fallback"`` to ask the caller to retry the whole session
    without TLS (``may`` only), or an error string to defer delivery.
    """
    if level == "none":
        return None
    if not client.has_extn("starttls"):
        if level == "secure":
            return (
                "STARTTLS not advertised by server "
                "(required by smtp_tls_security_level=secure)"
            )
        return None
    try:
        code, msg = client.starttls(context=_build_tls_context(level))
        logger.debug("SMTP: STARTTLS response: %s %s", code, msg)
        if not 200 <= code <= 299:
            raise RuntimeError(f"STARTTLS rejected: {code} {msg}")
        code, msg = client.ehlo(sender_hostname)
        logger.debug("SMTP: EHLO2 response: %s %s", code, msg)
        if not 200 <= code <= 299:
            raise RuntimeError(f"EHLO after STARTTLS failed: {code} {msg}")
        return None
    except Exception as e:  # pylint: disable=broad-exception-caught
        if level == "may":
            logger.warning(
                "SMTP: STARTTLS failed: %s, falling back to unencrypted socket", e
            )
            return "fallback"
        logger.error("SMTP: Failed to send email with TLS: %s", e, exc_info=True)
        return "Failed to send email with TLS"


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
    proxy: Optional[SmtpProxy] = None,
    smtp_ip: Optional[str] = None,
    smtp_tls_security_level: Optional[str] = "may",
) -> Dict[str, Any]:
    """
    Send an email via SMTP.

    Args:
        smtp_host: SMTP server hostname
        smtp_ip: SMTP server IP address (optional)
        smtp_port: SMTP server port
        envelope_from: Sender email address
        recipient_emails: Set of recipient email addresses
        message_content: Raw email message (bytes)
        smtp_username: SMTP username (optional)
        smtp_password: SMTP password (optional)
        timeout: Connection timeout in seconds
        proxy: Optional SOCKS5 proxy and local hostname to present in EHLO/HELO
        smtp_tls_security_level: SMTP TLS security level ("none", "may", "secure")

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
    sender_hostname = proxy.sender_hostname if proxy else None
    proxy_host = proxy.host if proxy else None

    def error_for_all_recipients(error: str, retry: bool) -> Dict[str, Any]:
        return {
            email: {
                "delivered": False,
                "error": error,
                "retry": retry,
                "smtp_host": smtp_host,
                "proxy_host": proxy_host,
            }
            for email in recipient_emails
        }

    client = ProxySMTP(
        host=None,
        port=None,
        timeout=timeout,
        proxy_host=proxy_host,
        proxy_port=proxy.port if proxy else None,
        proxy_username=proxy.username if proxy else None,
        proxy_password=proxy.password if proxy else None,
        local_hostname=sender_hostname,
    )

    def _quit():
        """Close the connection, sending a QUIT command to be polite but ignoring any errors"""
        try:
            client.quit()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("SMTP: QUIT failed %s", e)

    try:
        client._host = smtp_host  # noqa: SLF001 # pylint: disable=protected-access
        (code, msg) = client.connect(smtp_ip or smtp_host, smtp_port)
        logger.debug(
            "SMTP: connected to %s:%s (%s %s)", smtp_host, smtp_port, code, msg
        )
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

        tls_result = _starttls_upgrade(client, smtp_tls_security_level, sender_hostname)
        if tls_result == "fallback":
            _quit()
            return send_smtp_mail(
                smtp_host=smtp_host,
                smtp_ip=smtp_ip,
                smtp_port=smtp_port,
                envelope_from=envelope_from,
                recipient_emails=recipient_emails,
                message_content=message_content,
                smtp_username=smtp_username,
                smtp_password=smtp_password,
                timeout=timeout,
                proxy=proxy,
                smtp_tls_security_level="none",
            )
        if tls_result is not None:
            _quit()
            return error_for_all_recipients(tls_result, True)

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
                "smtp_host": smtp_host,
                "proxy_host": proxy_host,
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
            statuses[recipient_email] = {
                "delivered": True,
                "smtp_host": smtp_host,
                "proxy_host": proxy_host,
            }
        else:
            code_msg = recipient_errors[recipient_email]
            statuses[recipient_email] = {
                "delivered": False,
                "error": f"Recipient refused: {code_msg[0]} {code_msg[1]}",  # (code, msg)
                "retry": 400 <= code_msg[0] <= 499,
                "smtp_host": smtp_host,
                "proxy_host": proxy_host,
            }

    return statuses
