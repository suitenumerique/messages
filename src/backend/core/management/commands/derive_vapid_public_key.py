"""Derive the VAPID public key from the configured private key.

Run once when setting up Web Push: it prints the base64url public key that the
browser passes as ``applicationServerKey``. Pin the printed value in the
``PUSH_VAPID_PUBLIC_KEY`` env var so ``/config`` can serve it without importing
the push/crypto dependency graph on the request path.

``--verify`` instead checks that the *configured* ``PUSH_VAPID_PUBLIC_KEY``
matches what this private key derives to — a fast, no-startup-cost way to catch
the silent-failure case where the pinned public key has drifted from the private
key (e.g. after a key rotation), which makes all web push fail VAPID checks.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.services.push import derive_vapid_public_key


class Command(BaseCommand):
    """Print (or verify) the VAPID public key derived from the private key."""

    help = "Derive (or --verify) the VAPID public key (base64url) from the private key."

    def add_arguments(self, parser):
        parser.add_argument(
            "--private-key",
            type=str,
            default=None,
            help=(
                "VAPID private key (PEM or base64url). Defaults to the "
                "PUSH_VAPID_PRIVATE_KEY setting."
            ),
        )
        parser.add_argument(
            "--verify",
            action="store_true",
            help=(
                "Check that the configured PUSH_VAPID_PUBLIC_KEY matches the key "
                "derived from the private key, instead of printing it. Exits "
                "non-zero on a mismatch (or if either value is missing)."
            ),
        )

    def handle(self, *args, **options):
        private_key = options["private_key"] or settings.PUSH_VAPID_PRIVATE_KEY
        if not private_key:
            raise CommandError(
                "No VAPID private key: pass --private-key or set "
                "PUSH_VAPID_PRIVATE_KEY."
            )

        derived = derive_vapid_public_key(private_key)
        if not derived:
            raise CommandError("Could not derive a public key from the private key.")

        if options["verify"]:
            configured = settings.PUSH_VAPID_PUBLIC_KEY
            if not configured:
                raise CommandError(
                    "PUSH_VAPID_PUBLIC_KEY is not set; expected the derived value:\n"
                    f"  {derived}"
                )
            if configured != derived:
                raise CommandError(
                    "PUSH_VAPID_PUBLIC_KEY does NOT match the private key. "
                    "Web push will fail VAPID verification.\n"
                    f"  configured: {configured}\n"
                    f"  derived:    {derived}"
                )
            self.stdout.write(
                self.style.SUCCESS("PUSH_VAPID_PUBLIC_KEY matches the private key.")
            )
            return

        self.stdout.write(derived)
        self.stderr.write(
            self.style.SUCCESS(
                "Set this value as PUSH_VAPID_PUBLIC_KEY to enable Web Push."
            )
        )
