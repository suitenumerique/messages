"""Parse a raw RFC 5322 message and print the JMAP Email shape.

Run from the repository root after ``pip install -e src/jmap-email``::

    python src/jmap-email/examples/parse_and_print.py
"""

import json

from jmap_email import parse_email

RAW = (
    b"From: =?utf-8?B?QWxpY2U=?= <alice@example.com>\r\n"
    b"To: Bob <bob@example.com>\r\n"
    b"Subject: =?utf-8?Q?Hello_world?=\r\n"
    b"Date: Mon, 08 Jun 2026 12:00:00 +0000\r\n"
    b"Message-ID: <demo@example.com>\r\n"
    b'Content-Type: text/plain; charset="utf-8"\r\n'
    b"\r\n"
    b"Hi there - this is the parsed-and-printed example.\r\n"
)


def main() -> None:
    parsed = parse_email(RAW)

    # Drop binary attachment bodies before printing — the demo input
    # doesn't carry any, but real input often does and JSON can't
    # serialise ``bytes``.
    for attachment in parsed.get("attachments") or []:
        attachment.pop("content", None)

    print(json.dumps(parsed, indent=2, default=str))


if __name__ == "__main__":
    main()
