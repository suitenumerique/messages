"""Build a reply to a parsed message and verify the threading headers.

``make_reply`` produces a JMAP Email dict pre-filled with subject,
``inReplyTo``, ``references``, and recipients. The caller adds ``from``
and ``sentAt``, then composes.
"""

from datetime import datetime, timezone

from jmap_email import compose_email, make_reply, parse_email

ORIGINAL = (
    b"From: Bob <bob@example.com>\r\n"
    b"To: Alice <alice@example.com>\r\n"
    b"Subject: Lunch?\r\n"
    b"Date: Mon, 08 Jun 2026 11:00:00 +0000\r\n"
    b"Message-ID: <thread-root@example.com>\r\n"
    b'Content-Type: text/plain; charset="utf-8"\r\n'
    b"\r\n"
    b"Want to grab lunch?\r\n"
)


def main() -> None:
    original = parse_email(ORIGINAL)

    reply = make_reply(original, body_text="Yes, see you at 12:30.")
    reply["from"] = [{"name": "Alice", "email": "alice@example.com"}]
    reply["sentAt"] = datetime(2026, 6, 8, 11, 5, tzinfo=timezone.utc).isoformat()

    raw = compose_email(reply)

    # Re-parse to confirm threading survives a wire round-trip.
    on_wire = parse_email(raw)
    assert on_wire["subject"] == "Re: Lunch?"
    assert on_wire["inReplyTo"] == ["thread-root@example.com"]
    assert on_wire["references"] == ["thread-root@example.com"]

    print("OK: reply built with In-Reply-To / References intact")
    print(raw.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    main()
