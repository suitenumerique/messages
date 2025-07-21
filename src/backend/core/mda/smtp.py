import logging
import smtplib
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

def send_smtp_mail(
    smtp_host: str,
    smtp_port: int,
    envelope_from: str,
    recipient_emails: List[str],
    message_content: bytes,
    use_tls: bool = False,
    username: Optional[str] = None,
    password: Optional[str] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    """
    Send an email via SMTP.

    Args:
        smtp_host: SMTP server hostname
        smtp_port: SMTP server port
        envelope_from: Sender email address
        recipient_emails: List of recipient email addresses
        message_content: Raw email message (bytes)
        use_tls: Whether to use STARTTLS
        username: SMTP username (optional)
        password: SMTP password (optional)
        timeout: Connection timeout in seconds

    Returns:
        Dict mapping recipient emails to delivery status or error
    """
    statuses = {}
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=timeout) as client:
            client.ehlo()
            if use_tls:
                client.starttls()
                client.ehlo()
            if username and password:
                client.login(username, password)
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
        logger.error("SMTP error sending message: %s", e)
        for email in recipient_emails:
            statuses[email] = {"error": str(e), "delivered": False}
    return statuses 