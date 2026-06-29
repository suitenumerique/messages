"""Compose a non-ASCII Subject and re-parse it — pin the
RFC 2047 encoded-word round-trip.

Subjects, display names, and any other text-mode header values get
encoded automatically by the composer's strict SMTP policy when they
carry non-ASCII characters. The parser decodes them back into the
``parsed["subject"]`` convenience property.
"""

from datetime import datetime, timezone

from jmap_email import compose_email, parse_email


def main() -> None:
    raw = compose_email(
        {
            "from": [{"name": "Élise", "email": "elise@example.com"}],
            "to": [{"name": "Søren", "email": "soren@example.com"}],
            "subject": "Réunion à 14h — café ☕",
            "sentAt": datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc).isoformat(),
            "textBody": [
                {"partId": "1", "type": "text/plain", "content": "À demain !"}
            ],
        }
    )

    # The wire bytes carry RFC 2047 encoded-words; the Subject is
    # base64- or quoted-printable-encoded depending on stdlib choice.
    assert b"=?utf-8?" in raw, "subject did not get encoded for transport"

    # Parsing returns the decoded form on the convenience property.
    parsed = parse_email(raw)
    assert parsed["subject"] == "Réunion à 14h — café ☕"
    print(f"OK: subject decoded back to {parsed['subject']!r}")


if __name__ == "__main__":
    main()
