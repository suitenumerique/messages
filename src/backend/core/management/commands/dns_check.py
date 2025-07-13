"""
Django management command to check DNS records for mail domains.
"""

from django.core.management.base import BaseCommand, CommandError

from core.dns.check import check_dns_records
from core.models import MailDomain


class Command(BaseCommand):
    help = "Check DNS records for mail domains"

    def add_arguments(self, parser):
        parser.add_argument(
            "--domain",
            type=str,
            help="Specific domain to check (if not provided, checks all domains)",
        )

    def handle(self, *args, **options):
        domain_name = options["domain"]

        if domain_name:
            try:
                maildomain = MailDomain.objects.get(name=domain_name)
                domains = [maildomain]
            except MailDomain.DoesNotExist:
                raise CommandError(f"Domain '{domain_name}' not found") from None
        else:
            domains = MailDomain.objects.all()

        self.stdout.write(f"Checking DNS records for {len(domains)} domain(s)...")
        self.stdout.write("")

        for maildomain in domains:
            self.check_domain(maildomain)

    def check_domain(self, maildomain):
        """Check DNS records for a specific domain."""
        domain = maildomain.name

        self.stdout.write(f"Domain: {domain}")
        self.stdout.write("-" * (len(domain) + 8))

        # Get DNS check results
        check_results = check_dns_records(maildomain)

        self.print_detailed_results(check_results)

        self.stdout.write("")

    def print_detailed_results(self, check_results):
        """Print a flat list of DNS check results with status emojis."""
        status_emoji = {
            "correct": "🟢",
            "incorrect": "🟡",
            "missing": "🔴",
            "error": "⚠️",
        }
        for record in check_results:
            status = record["_check"]["status"]
            emoji = status_emoji.get(status, "❓")
            target = record["target"] or "@"
            line = f"{emoji} {record['type']} record for {target}"
            if status == "correct":
                line += f" — Value: {record['value']}"
            elif status == "incorrect":
                line += f" — Expected: {record['value']} | Found: {', '.join(record['_check'].get('found', []))}"
            elif status == "missing":
                line += f" — Expected: {record['value']} | Error: {record['_check'].get('error', '')}"
            elif status == "error":
                line += f" — Error: {record['_check'].get('error', '')}"
            self.stdout.write(line)
        self.stdout.write("")
