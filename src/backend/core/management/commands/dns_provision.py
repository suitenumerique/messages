"""
Django management command to provision DNS records for mail domains.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.dns.provisioning import (
    detect_dns_provider,
    provision_domain_dns,
)
from core.models import MailDomain


class Command(BaseCommand):
    help = "Provision DNS records for mail domains"

    def add_arguments(self, parser):
        parser.add_argument("--domain", type=str, help="Domain name to provision")
        parser.add_argument("--domainid", type=int, help="Domain ID to provision")
        parser.add_argument(
            "--provider",
            type=str,
            help="DNS provider to use (auto-detect if not specified)",
        )
        parser.add_argument(
            "--pretend",
            action="store_true",
            help="Simulate provisioning without making actual changes",
        )

    def handle(self, *args, **options):
        domain_name = options["domain"]
        domain_id = options["domainid"]
        provider_name = options["provider"]
        pretend = options["pretend"]

        if not domain_name and not domain_id:
            raise CommandError("Either --domain or --domainid must be specified")

        if domain_name and domain_id:
            raise CommandError("Cannot specify both --domain and --domainid")

        try:
            if domain_name:
                maildomain = MailDomain.objects.get(name=domain_name)
            else:
                maildomain = MailDomain.objects.get(id=domain_id)
        except MailDomain.DoesNotExist:
            if domain_name:
                raise CommandError(f"Domain '{domain_name}' not found") from None
            else:
                raise CommandError(f"Domain with ID {domain_id} not found") from None

        if pretend:
            self.stdout.write(
                self.style.WARNING("PRETEND MODE: No actual changes will be made")
            )
            self.stdout.write("")

        self.process_domain(maildomain, provider_name, pretend)

    def process_domain(self, maildomain, provider_name, pretend):
        """Process a single domain for DNS provisioning."""
        domain = maildomain.name

        self.stdout.write(f"Domain: {domain}")
        self.stdout.write("-" * (len(domain) + 8))

        # Show provider information
        detected_provider = detect_dns_provider(domain)
        if detected_provider:
            self.stdout.write(f"Detected provider: {detected_provider}")
        else:
            self.stdout.write(self.style.WARNING("No provider detected"))

        # Check if we can provision
        can_provision = (
            provider_name or detected_provider or settings.DNS_DEFAULT_PROVIDER
        )
        if not can_provision:
            self.stdout.write(
                self.style.ERROR("✗ Cannot provision DNS records for this domain")
            )
            self.stdout.write("")
            return

        self.provision_domain(maildomain, provider_name, pretend)
        self.stdout.write("")

    def provision_domain(self, maildomain, provider_name, pretend):
        """Provision DNS records for a domain."""

        if pretend:
            self.stdout.write("Simulating DNS record provisioning...")
        else:
            self.stdout.write("Provisioning DNS records...")

        results = provision_domain_dns(
            maildomain, provider_name=provider_name, pretend=pretend
        )

        if results["success"]:
            if pretend:
                self.stdout.write(
                    self.style.SUCCESS("✓ DNS provisioning simulation successful")
                )
            else:
                self.stdout.write(self.style.SUCCESS("✓ DNS provisioning successful"))

            # Show which provider was used
            provider_used = results.get("provider", "unknown")
            if provider_used:
                self.stdout.write(f"Provider used: {provider_used}")

            if results["created"]:
                if pretend:
                    self.stdout.write(
                        f"Would create {len(results['created'])} records:"
                    )
                else:
                    self.stdout.write(f"Created {len(results['created'])} records:")
                for record in results["created"]:
                    self.stdout.write(
                        f"  - {record['type']} record for {record['name']}: {record['value']}"
                    )

            if results["updated"]:
                if pretend:
                    self.stdout.write(
                        f"Would update {len(results['updated'])} records:"
                    )
                else:
                    self.stdout.write(f"Updated {len(results['updated'])} records:")
                for record in results["updated"]:
                    self.stdout.write(
                        f"  - {record['type']} record for {record['name']}"
                    )
                    self.stdout.write(f"    Old: {record['old_value']}")
                    self.stdout.write(f"    New: {record['new_value']}")

            if results["errors"]:
                self.stdout.write(
                    self.style.WARNING(f"Errors ({len(results['errors'])}):")
                )
                for error in results["errors"]:
                    self.stdout.write(
                        f"  - {error['type']} record for {error['name']}: {error['error']}"
                    )
        elif pretend:
            self.stdout.write(
                self.style.ERROR(
                    f"✗ DNS provisioning simulation failed: {results['error']}"
                )
            )
        else:
            self.stdout.write(
                self.style.ERROR(f"✗ DNS provisioning failed: {results['error']}")
            )
