"""Management command to create storage buckets and configure lifecycle rules."""

from django.core.files.storage import storages
from django.core.management.base import BaseCommand

from botocore.exceptions import ClientError


class Command(BaseCommand):
    """Create a storage bucket and optionally set a lifecycle expiration rule."""

    help = "Create a storage bucket and optionally set a lifecycle expiration rule"

    def add_arguments(self, parser):
        parser.add_argument(
            "--storage",
            help="Storage backend to create the bucket for",
            choices=storages.backends.keys(),
            required=True,
        )
        parser.add_argument(
            "--expire-days",
            type=int,
            default=0,
            help="Auto-expire objects after this many days (0 = no expiration)",
        )

    def handle(self, *args, **options):
        storage = storages[options["storage"]]
        s3_client = storage.connection.meta.client
        bucket = storage.bucket_name

        # Create the bucket if it doesn't exist
        try:
            s3_client.head_bucket(Bucket=bucket)
            self.stdout.write(f"Bucket '{bucket}' already exists.")
        except ClientError:
            s3_client.create_bucket(Bucket=bucket)
            self.stdout.write(self.style.SUCCESS(f"Bucket '{bucket}' created."))

        # Set lifecycle expiration rule
        expire_days = options["expire_days"]
        if expire_days > 0:
            s3_client.put_bucket_lifecycle_configuration(
                Bucket=bucket,
                LifecycleConfiguration={
                    "Rules": [
                        {
                            "ID": "auto-expire",
                            "Status": "Enabled",
                            "Expiration": {"Days": expire_days},
                            "Filter": {"Prefix": ""},
                        }
                    ]
                },
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Lifecycle rule set: objects expire after {expire_days} day(s)."
                )
            )
