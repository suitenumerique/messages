import logging

from django.core.management.base import BaseCommand, CommandError

from keycloak.exceptions import KeycloakError

from core.identity.keycloak import (
    list_keycloak_users,
    reset_keycloak_user_password,
    resync_all_mailboxes_to_keycloak,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Identity management commands for Keycloak integration"

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="command", help="Available commands")

        # List users command
        list_parser = subparsers.add_parser("list-users", help="List all users")
        list_parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Maximum number of users to return (default: 100)",
        )

        # Reset password command
        reset_parser = subparsers.add_parser(
            "reset-password", help="Reset user password with one-time new password"
        )
        reset_parser.add_argument(
            "email", help="Email of the user to reset password for"
        )
        reset_parser.add_argument(
            "--new-password",
            help="New password (if not provided, one will be generated)",
        )

        # Resync all command
        subparsers.add_parser(
            "resync-all",
            help="Resync all mailboxes with identity_sync enabled to Keycloak",
        )

    def handle(self, *args, **options):
        command = options.get("command")

        if not command:
            self.print_help("manage.py", "identity")
            return

        try:
            if command == "list-users":
                self.list_users(options)
            elif command == "reset-password":
                self.reset_password(options)
            elif command == "resync-all":
                self.resync_all(options)
            else:
                raise CommandError(f"Unknown command: {command}")

        except KeycloakError as e:
            raise CommandError(f"Keycloak error: {e}") from e

    def list_users(self, options):
        """List all users in the realm."""
        limit = options.get("limit", 100)

        self.stdout.write(self.style.SUCCESS(f"Fetching up to {limit} users..."))

        users = list_keycloak_users(limit)

        if not users:
            self.stdout.write(self.style.WARNING("No users found."))
            return

        self.stdout.write(self.style.SUCCESS(f"Found {len(users)} users:"))
        self.stdout.write("")

        # Print header
        self.stdout.write(
            f"{'Username':<30} {'Email':<30} {'First Name':<15} {'Last Name':<15} {'Enabled':<8}"
        )
        self.stdout.write("-" * 98)

        for user in users:
            username = user.get("username", "N/A")
            email = user.get("email", "N/A")
            first_name = user.get("firstName", "N/A")
            last_name = user.get("lastName", "N/A")
            enabled = "Yes" if user.get("enabled", False) else "No"

            self.stdout.write(
                f"{username:<30} {email:<30} {first_name:<15} {last_name:<15} {enabled:<8}"
            )

    def reset_password(self, options):
        """Reset user password with a one-time new password."""
        new_password = options.get("new_password")

        self.stdout.write(
            self.style.SUCCESS(f'Resetting password for user "{options["email"]}"...')
        )

        try:
            generated_password = reset_keycloak_user_password(
                options["email"], new_password
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f'Password reset successfully for user "{options["email"]}"!'
                )
            )
            self.stdout.write(f"New temporary password: {generated_password}")
            self.stdout.write(
                self.style.WARNING(
                    "Note: The user will be required to change this password on next login."
                )
            )

        except ValueError as e:
            raise CommandError(str(e)) from e

    def resync_all(self, options):
        """Resync all mailboxes with identity_sync enabled to Keycloak."""
        self.stdout.write(
            self.style.SUCCESS("Starting resync of all mailboxes to Keycloak...")
        )

        result = resync_all_mailboxes_to_keycloak()
        self.stdout.write(self.style.SUCCESS("Resync completed!"))
        self.stdout.write(f"Synced domains: {result['synced_domains']}")
        self.stdout.write(f"Synced mailboxes: {result['synced_mailboxes']}")
