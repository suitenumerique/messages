"""Standalone SMTP client for sending emails, with support for SOCKS5 proxies."""

import logging
import smtplib
import socket
import ssl
import struct
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ProxyError(Exception):
    """Exception raised when SOCKS5 proxy operations fail."""


def create_proxied_connection(
    proxy_host,
    proxy_port,
    target_host,
    target_port,
    proxy_username=None,
    proxy_password=None,
    timeout=5,
    proxy_tls=False,
):
    """
    Create a SOCKS5 proxy connection to a target host.

    Args:
        proxy_host: SOCKS5 proxy hostname
        proxy_port: SOCKS5 proxy port
        target_host: Target hostname to connect to
        target_port: Target port to connect to
        proxy_username: SOCKS5 proxy username (optional)
        proxy_password: SOCKS5 proxy password (optional)
        timeout: Connection timeout in seconds
        proxy_tls: Whether to wrap the proxy connection in TLS

    Returns:
        Tuple of (socket, remote_addr, remote_port)

    Raises:
        ProxyError: If the proxy connection fails
    """
    has_auth = proxy_username is not None and proxy_password is not None

    raw_sock = socket.create_connection((proxy_host, proxy_port))

    # This is the reason we reimplement PySocks here: to be able to wrap the socket in TLS
    if proxy_tls:
        tls_sock = ssl.create_default_context().wrap_socket(raw_sock)
    else:
        tls_sock = raw_sock

    if type(timeout) in {int, float}:
        tls_sock.settimeout(timeout)

    # SOCKS5 handshake
    auth_bit = b"\x00" if not has_auth else b"\x02"
    tls_sock.sendall(b"\x05\x01" + auth_bit)  # version 5, 1 auth method, auth bit
    resp = tls_sock.recv(2)
    if resp[0] != 0x05:
        raise ProxyError("SOCKS5 server does not support version 5")
    if has_auth and resp[1] != 0x02:
        raise ProxyError("SOCKS5 server does not support auth")
    if not has_auth and resp[1] != 0x00:
        raise ProxyError("SOCKS5 server does not support anon")

    # Send authentication if needed
    if has_auth:
        tls_sock.sendall(
            b"\x01"
            + chr(len(proxy_username)).encode()
            + proxy_username.encode("utf-8")
            + chr(len(proxy_password)).encode()
            + proxy_password.encode("utf-8")
        )
        resp = tls_sock.recv(2)
        if resp[0] != 0x01:
            raise ProxyError("SOCKS5 server sent bad data after auth")
        if resp[1] != 0x00:
            raise ProxyError("SOCKS5 authentication failed")

    # SOCKS5 connect request (IPv4 is resolved locally)
    addr = socket.gethostbyname(target_host)
    port_bytes = struct.pack(">H", target_port)
    request = b"\x05\x01\x00\x01" + socket.inet_aton(addr) + port_bytes
    tls_sock.sendall(request)
    resp = tls_sock.recv(3)
    if resp[0] != 0x05:
        raise ProxyError("SOCKS5 server sent bad data after connect")
    if resp[1] != 0x00:
        raise ProxyError("SOCKS5 connection failed")

    # Parse response address and port
    atyp = tls_sock.recv(1)
    if atyp == b"\x01":
        remote_addr = socket.inet_ntoa(tls_sock.recv(4))
    elif atyp == b"\x03":
        length = tls_sock.recv(1)
        remote_addr = tls_sock.recv(ord(length))
    elif atyp == b"\x04":
        remote_addr = socket.inet_ntop(socket.AF_INET6, tls_sock.recv(16))
    else:
        raise ProxyError("SOCKS5 proxy server sent invalid addr data")

    remote_port = struct.unpack(">H", tls_sock.recv(2))[0]

    # Now we can return the socket properly connected through the proxy
    return tls_sock, remote_addr, remote_port


def create_proxied_socket(*args, **kwargs):
    """
    Create a SOCKS5 proxy socket connection.

    Args:
        *args: Arguments passed to create_proxied_connection
        **kwargs: Keyword arguments passed to create_proxied_connection

    Returns:
        Socket connection through the proxy
    """
    return create_proxied_connection(*args, **kwargs)[0]


class ProxySMTP(smtplib.SMTP):
    """SMTP client that connects through a SOCKS5 proxy."""

    def __init__(self, host, port, *args, **kwargs):
        if "proxy_host" in kwargs:
            self.proxy_host = kwargs.pop("proxy_host")
        if "proxy_port" in kwargs:
            self.proxy_port = kwargs.pop("proxy_port")
        if "proxy_username" in kwargs:
            self.proxy_username = kwargs.pop("proxy_username")
        if "proxy_password" in kwargs:
            self.proxy_password = kwargs.pop("proxy_password")
        if "proxy_tls" in kwargs:
            self.proxy_tls = kwargs.pop("proxy_tls")
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

        # This makes it simpler for SMTP_SSL to use the SMTP connect code
        # and just alter the socket connection bit.
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
            self.proxy_tls,
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
    proxy_tls: bool = False,
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
        proxy_tls: Whether to wrap the proxy connection in TLS
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
            proxy_tls=proxy_tls,
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
                client.login(smtp_username, smtp_password)

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
    except (smtplib.SMTPException, OSError, ProxyError) as e:
        logger.error("SMTP error sending message: %s", e)
        for email in recipient_emails:
            statuses[email] = {"error": str(e), "delivered": False}
    return statuses
