"""Read an .eml file off disk, parse it safely, surface key fields.

Demonstrates the two failure surfaces a real consumer has to handle:

1. ``parse_email`` returns ``None`` when the input is fundamentally
   unparseable (empty bytes, wrong type, an unrecoverable internal
   error). Every failure logs at WARNING; the call site decides
   whether to log + skip, return 400, quarantine, etc.

2. Recoverable damage (a salvageable malformed header, an unknown
   charset that fell back to utf-8/replace, …) leaves the parse on
   track and surfaces in ``parsed["_ext"]["defects"]``. Strict
   importers can quarantine on a non-empty defect list; lenient ones
   can flag and move on.

Run from the repository root::

    python src/jmap-email/examples/import_eml_safely.py path/to/file.eml
"""

import sys
from pathlib import Path

from jmap_email import body_text_joined, first_address, parse_email


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <path/to/file.eml>", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    try:
        raw = path.read_bytes()
    except OSError as e:
        print(f"Cannot read {path}: {e}", file=sys.stderr)
        return 1

    # ``extensions=True`` opts into the project-extension
    # namespace ``parsed["_ext"]`` — defects + Resent-* projection.
    parsed = parse_email(raw, extensions=True)
    if parsed is None:
        print(f"{path}: unparseable, skipping", file=sys.stderr)
        return 1

    defects = (parsed.get("_ext") or {}).get("defects") or []
    if defects:
        print(f"defects: {sorted(set(defects))}", file=sys.stderr)

    sender = first_address(parsed.get("from")) or {}
    print(f"subject: {parsed.get('subject')!r}")
    print(f"from:    {sender.get('name')!r} <{sender.get('email', '')}>")
    print(f"sentAt:  {parsed.get('sentAt')}")
    print(f"preview: {parsed.get('preview')!r}")

    body = body_text_joined(parsed)
    snippet = body if len(body) <= 200 else body[:200] + "..."
    print(f"body:    {snippet!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
