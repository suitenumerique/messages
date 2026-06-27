"""Generate a fresh VAPID keypair for Web Push.

Run once when setting up Web Push. It prints a new base64url private key (the
single-line form accepted by ``PUSH_VAPID_PRIVATE_KEY``) together with its
matching ``PUSH_VAPID_PUBLIC_KEY`` — pin both env vars and the pair is
guaranteed in sync. The private key is a secret: keep it out of logs and VCS.
"""

from django.core.management.base import BaseCommand

from core.services.push import generate_vapid_keypair


class Command(BaseCommand):
    """Print a fresh VAPID keypair (base64url private + public keys)."""

    help = "Generate a new VAPID keypair (base64url) for Web Push."

    def handle(self, *args, **options):
        private_b64, public_b64 = generate_vapid_keypair()

        # The keys go to stdout (pipe/capture-friendly); the guidance to stderr
        # so it never contaminates a value the operator redirects to a file.
        self.stdout.write(private_b64)
        self.stderr.write(
            self.style.SUCCESS(
                "Set the following env vars to enable Web Push:\n"
                f"  PUSH_VAPID_PRIVATE_KEY={private_b64}\n"
                f"  PUSH_VAPID_PUBLIC_KEY={public_b64}\n"
                "Keep the private key secret."
            )
        )
