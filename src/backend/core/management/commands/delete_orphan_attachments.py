"""Delete orphan attachments that are not linked to any message."""

from django.core.management.base import BaseCommand, CommandError

from core import models


class Command(BaseCommand):
    """Delete orphan attachments that are not linked to any message."""

    help = "Deletes attachments (and their blobs) that are not linked to any message."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted without actually deleting.",
        )
        parser.add_argument(
            "--domain",
            type=str,
            help="Filter by mail domain name (e.g., 'example.com').",
        )
        parser.add_argument(
            "--mailbox",
            type=str,
            help="Filter by mailbox email address (e.g., 'contact@example.com').",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="delete_all",
            help="Delete all orphan attachments (required if no filter is provided).",
        )

    def handle(self, *args, **options):
        """Delete orphan attachments and their blobs."""

        dry_run = options["dry_run"]
        domain_name = options.get("domain")
        mailbox_email = options.get("mailbox")
        delete_all = options.get("delete_all")

        # Require either a filter or --all flag
        if not domain_name and not mailbox_email and not delete_all:
            raise CommandError(
                "You must provide --domain, --mailbox, or --all to proceed."
            )

        # Find orphan attachments (not linked to any message)
        orphan_attachments = models.Attachment.objects.filter(messages__isnull=True)

        # Apply filters
        if domain_name:
            orphan_attachments = orphan_attachments.filter(
                mailbox__domain__name=domain_name
            )
            self.stdout.write(f"Filtering by domain: {domain_name}")

        if mailbox_email:
            # Parse email to get local_part and domain
            if "@" not in mailbox_email:
                raise CommandError(
                    f"Invalid mailbox email format: {mailbox_email}. "
                    "Expected format: 'local_part@domain.com'."
                )
            local_part, domain = mailbox_email.split("@", 1)
            orphan_attachments = orphan_attachments.filter(
                mailbox__local_part=local_part,
                mailbox__domain__name=domain,
            )
            self.stdout.write(f"Filtering by mailbox: {mailbox_email}")

        orphan_count = orphan_attachments.count()

        if orphan_count == 0:
            self.stdout.write(self.style.SUCCESS("No orphan attachments found."))
            return

        # Get blob IDs to check which ones would be deleted
        blob_ids = list(orphan_attachments.values_list("blob_id", flat=True))

        if dry_run:
            # For dry-run, we need to simulate: blobs whose only attachment is orphan
            # A blob is deletable if all its attachments are in orphan_attachments
            deletable_blobs = models.Blob.objects.filter(
                id__in=blob_ids,
                messages__isnull=True,
                draft__isnull=True,
            ).exclude(
                # Exclude blobs that have attachments NOT in the orphan list
                attachments__in=models.Attachment.objects.exclude(
                    id__in=orphan_attachments.values_list("id", flat=True)
                )
            )
            orphan_blob_count = deletable_blobs.count()

            self.stdout.write(
                f"[DRY RUN] Would delete {orphan_count} attachment(s) "
                f"and {orphan_blob_count} blob(s)."
            )
            return

        # Delete orphan attachments
        deleted_attachments, _ = orphan_attachments.delete()

        # Delete blobs that are no longer referenced by anything
        deleted_blobs = 0
        if blob_ids:
            deleted_blobs, _ = models.Blob.objects.filter(
                id__in=blob_ids,
                attachments__isnull=True,  # no more attachments
                messages__isnull=True,  # not used by Message.blob
                draft__isnull=True,  # not used by Message.draft_blob
            ).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {deleted_attachments} attachment(s) and {deleted_blobs} blob(s)."
            )
        )
