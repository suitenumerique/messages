"""Compose a multipart/mixed message with a regular PDF attachment.

The output bytes are ready for ``smtplib.SMTP.sendmail`` — that path
applies RFC 5321 §4.5.2 dot-stuffing for you.
"""

import base64
from datetime import datetime, timezone

from jmap_email import compose_email

FAKE_PDF = b"%PDF-1.7\n% demo content\n%%EOF\n"


def main() -> None:
    raw = compose_email(
        {
            "from": [{"name": "Alice", "email": "alice@example.com"}],
            "to": [{"name": "Bob", "email": "bob@example.com"}],
            "subject": "Attached: invoice",
            "sentAt": datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc).isoformat(),
            "textBody": [
                {
                    "partId": "1",
                    "type": "text/plain",
                    "content": "Hi Bob,\nInvoice attached.\nAlice\n",
                }
            ],
            "attachments": [
                {
                    "name": "invoice.pdf",
                    "type": "application/pdf",
                    "content": base64.b64encode(FAKE_PDF).decode("ascii"),
                    "disposition": "attachment",
                }
            ],
        }
    )
    print(raw.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    main()
