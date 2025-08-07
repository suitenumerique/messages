"""Management command to run the self-check functionality."""

from django.core.management.base import BaseCommand
from django.conf import settings

from core.mda.selfcheck import run_self_check


class Command(BaseCommand):
    """Run a selfcheck of the mail delivery system."""

    help = "Run an end-to-end selfcheck of the mail delivery system"

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Enable verbose output",
        )

    def handle(self, *args, **options):
        """Execute the command."""
        verbose = options["verbose"]
        
        if verbose:
            self.stdout.write("Starting selfcheck...")
            self.stdout.write(f"FROM: {settings.MESSAGES_SELFCHECK_FROM}")
            self.stdout.write(f"TO: {settings.MESSAGES_SELFCHECK_TO}")
            self.stdout.write(f"SECRET: {settings.MESSAGES_SELFCHECK_SECRET}")
            self.stdout.write("")
        
        # Run the selfcheck
        result = run_selfcheck()
        
        # Display results
        if result["success"]:
            self.stdout.write(
                self.style.SUCCESS("✓ Selfcheck completed successfully!")
            )
            
            if verbose:
                self.stdout.write("")
                self.stdout.write("Timings:")
                if result["send_time"] is not None:
                    self.stdout.write(f"  Send time: {result['send_time']:.2f}s")
                if result["reception_time"] is not None:
                    self.stdout.write(f"  Reception time: {result['reception_time']:.2f}s")
        else:
            self.stdout.write(
                self.style.ERROR(f"✗ Selfcheck failed: {result['error']}")
            )
        
        # Return appropriate exit code
        return 0 if result["success"] else 1 