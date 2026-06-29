"""Compose a message with an HTML body that references an inline image
via CID, then re-parse it and assert the CID round-trips intact.

A practical test of the multipart/related shape jmap-email emits when
``htmlBody`` and ``attachments`` carry inline parts.
"""

import base64
from datetime import datetime, timezone

from jmap_email import compose_email, parse_email

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
    b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
    b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)

CID = "logo@example.com"


def main() -> None:
    raw = compose_email(
        {
            "from": [{"email": "alice@example.com"}],
            "to": [{"email": "bob@example.com"}],
            "subject": "inline image demo",
            "sentAt": datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc).isoformat(),
            "htmlBody": [
                {
                    "partId": "1",
                    "type": "text/html",
                    "content": f'<p>Look: <img src="cid:{CID}"></p>',
                }
            ],
            "attachments": [
                {
                    "name": "logo.png",
                    "type": "image/png",
                    "content": base64.b64encode(PNG_1X1).decode("ascii"),
                    "disposition": "inline",
                    "cid": CID,
                }
            ],
        }
    )

    parsed = parse_email(raw)
    inline = next(
        (a for a in parsed.get("attachments") or [] if a.get("disposition") == "inline"),
        None,
    )
    assert inline is not None, "expected an inline attachment on round-trip"
    assert inline.get("cid") == CID, (
        f"cid did not round-trip: composed={CID!r}, parsed={inline.get('cid')!r}"
    )
    print(f"OK: inline image '{inline['name']}' round-tripped with cid <{inline['cid']}>")


if __name__ == "__main__":
    main()
