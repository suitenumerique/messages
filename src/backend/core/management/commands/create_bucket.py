"""Management command to create storage buckets and configure lifecycle rules."""

import json

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
        parser.add_argument(
            "--public",
            action="store_true",
            help="Grant anonymous read access to all objects (public bucket)",
        )
        parser.add_argument(
            "--abort-incomplete-mpu-days",
            type=int,
            default=None,
            help=(
                "Abort dangling multipart uploads this many days after they were "
                "initiated (0 = off). Defaults to --expire-days when omitted, so a "
                "plain Expiration.Days rule doesn't silently leak incomplete "
                "multipart uploads (which it never cleans up on its own)."
            ),
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

        expire_days = options["expire_days"]
        # Track dangling multipart uploads on the same clock as object expiry
        # unless overridden; an ``Expiration.Days`` rule alone never aborts them.
        abort_mpu_days = options["abort_incomplete_mpu_days"]
        if abort_mpu_days is None:
            abort_mpu_days = expire_days

        rule = {"ID": "auto-expire", "Status": "Enabled", "Filter": {"Prefix": ""}}
        if expire_days > 0:
            rule["Expiration"] = {"Days": expire_days}
        if abort_mpu_days > 0:
            rule["AbortIncompleteMultipartUpload"] = {
                "DaysAfterInitiation": abort_mpu_days
            }

        # Only push a lifecycle config if the rule actually does something.
        if "Expiration" in rule or "AbortIncompleteMultipartUpload" in rule:
            s3_client.put_bucket_lifecycle_configuration(
                Bucket=bucket,
                LifecycleConfiguration={"Rules": [rule]},
            )
            parts = []
            if expire_days > 0:
                parts.append(f"objects expire after {expire_days} day(s)")
            if abort_mpu_days > 0:
                parts.append(
                    f"incomplete multipart uploads aborted after "
                    f"{abort_mpu_days} day(s)"
                )
            self.stdout.write(
                self.style.SUCCESS(f"Lifecycle rule set: {', '.join(parts)}.")
            )

        # Grant anonymous read access (e.g. mobile OTA bundles served directly
        # from the bucket). Only intended for non-sensitive, public artifacts.
        if options["public"]:
            s3_client.put_bucket_policy(
                Bucket=bucket,
                Policy=json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Sid": "PublicRead",
                                "Effect": "Allow",
                                "Principal": "*",
                                "Action": "s3:GetObject",
                                "Resource": f"arn:aws:s3:::{bucket}/*",
                            }
                        ],
                    }
                ),
            )
            self.stdout.write(
                self.style.SUCCESS(f"Bucket '{bucket}' is now publicly readable.")
            )
