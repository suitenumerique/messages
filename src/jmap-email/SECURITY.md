# Security policy

## Reporting a vulnerability

**Do not file a public issue** for security-sensitive reports.

Use either of the following private channels:

1. **GitHub Security Advisories** — preferred. Open a private advisory
   at <https://github.com/suitenumerique/messages/security/advisories/new>
   and tag the report with `jmap-email`.
2. **Email** — `security@suite.anct.gouv.fr`. PGP key on request.

Please include:

- Affected version (`jmap_email.__version__`)
- Reproducer: minimal input bytes / dict that triggers the issue
- Impact assessment in your own words

You can expect an initial acknowledgement within 5 business days.
Coordinated disclosure follows a 90-day embargo from receipt unless
the issue is already public, actively exploited, or the affected
parties agree to a different timeline.

## Scope

In scope:

- Parser crashes, hangs, or memory blow-ups on adversarial input
- Composer outputs that violate RFC 5322 / 5321 in ways that allow
  header injection, address-list smuggling, or DKIM signature breakage
- Bypasses of any defense listed in the README's defense matrix

Out of scope:

- Issues that require the operator to misconfigure resource caps
  beyond documented defaults
- Behaviour of the upstream Python `email` package not surfaced by
  this library (file those upstream)
- PGP / S/MIME / DKIM / SPF / ARC verification (not implemented)

## CVE credit

Reporters who follow the disclosure process above are credited in the
published advisory and in the corresponding `CHANGELOG.md` entry.
Anonymous credit is honoured on request.
