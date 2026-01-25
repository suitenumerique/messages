"""Util to generate S3 authorization headers for object storage access control"""

import hashlib
import uuid

from django.conf import settings

import boto3
import botocore

from core import models


def flat_to_nested(items):
    """
    Create a nested tree structure from a flat list of items.
    """
    # Create a dictionary to hold nodes by their path
    node_dict = {}
    roots = []

    # Sort the flat list by path to ensure parent nodes are processed first
    items.sort(key=lambda x: x["path"])

    for item in items:
        item["children"] = []  # Initialize children list
        node_dict[item["path"]] = item

        # Determine parent path
        parent_path = ".".join(item["path"].split(".")[:-1])

        if parent_path in node_dict:
            node_dict[parent_path]["children"].append(item)
        else:
            roots.append(item)  # Collect root nodes

    if len(roots) > 1:
        raise ValueError("More than one root element detected")

    return roots[0] if roots else {}


def get_file_key(user_id, filename):
    """Generate the file key to store file in the message imports bucket."""
    return hashlib.sha256(f"{user_id}-{filename}".encode("utf-8")).hexdigest()


def generate_presigned_url(storage, *args, **kwargs):
    """Generate a presigned URL for the message imports bucket."""
    # This settings should be used if the backend application and the frontend application
    # can't connect to the object storage with the same domain. This is the case in the
    # docker compose stack used in development. The frontend application will use localhost
    # to connect to the object storage while the backend application will use the object storage
    # service name declared in the docker compose stack.
    # This is needed because the domain name is used to compute the signature. So it can't be
    # changed dynamically by the frontend application.
    if replace_domain_url := settings.AWS_S3_DOMAIN_REPLACE:
        s3_client = boto3.client(
            "s3",
            aws_access_key_id=storage.access_key,
            aws_secret_access_key=storage.secret_key,
            endpoint_url=replace_domain_url,
            config=botocore.client.Config(
                region_name=storage.region_name,
                signature_version=storage.signature_version,
            ),
        )
    else:
        s3_client = storage.connection.meta.client

    return s3_client.generate_presigned_url(*args, **kwargs)


def get_attachment_from_blob_id(blob_id, user):
    """
    Parse a given blob ID to get the attachment data from the related message raw mime.
    Blob IDs in the form msg_[message_id]_[attachment_number] are looked up
    directly in the message's attachments.
    """
    if not blob_id.startswith("msg_"):
        raise ValueError("Invalid blob ID")

    blob_id_parts = blob_id.split("_")

    if len(blob_id_parts) != 3:
        raise ValueError("Invalid blob ID")

    try:
        message_id = uuid.UUID(blob_id_parts[1])
    except ValueError as exc:
        raise ValueError("Invalid message ID") from exc

    try:
        attachment_number = int(blob_id_parts[2])
    except ValueError as exc:
        raise ValueError("Invalid attachment number") from exc

    # Does the message exist?
    try:
        message = models.Message.objects.get(id=message_id)
    except models.Message.DoesNotExist as exc:
        raise models.Blob.DoesNotExist() from exc

    # Does the user have access to the message via its thread?
    if not models.ThreadAccess.objects.filter(
        thread=message.thread, mailbox__accesses__user=user
    ).exists():
        raise models.Blob.DoesNotExist()

    # Does the message have any attachments?
    if not message.has_attachments:
        raise models.Blob.DoesNotExist()

    # Parse the raw mime message to get the attachment
    parsed_email = message.get_parsed_data()
    attachments = parsed_email.get("attachments", [])

    if attachment_number < 0 or attachment_number >= len(attachments):
        raise models.Blob.DoesNotExist()

    attachment = attachments[attachment_number]

    if not attachment:
        raise models.Blob.DoesNotExist()

    return attachment


# def generate_s3_authorization_headers(key):
#     """
#     Generate authorization headers for an s3 object.
#     These headers can be used as an alternative to signed urls with many benefits:
#     - the urls of our files never expire and can be stored in our items' content
#     - we don't leak authorized urls that could be shared (file access can only be done
#       with cookies)
#     - access control is truly realtime
#     - the object storage service does not need to be exposed on internet
#     """
#     url = default_storage.unsigned_connection.meta.client.generate_presigned_url(
#         "get_object",
#         ExpiresIn=0,
#         Params={"Bucket": default_storage.bucket_name, "Key": key},
#     )
#     request = botocore.awsrequest.AWSRequest(method="get", url=url)

#     s3_client = default_storage.connection.meta.client
#     # pylint: disable=protected-access
#     credentials = s3_client._request_signer._credentials
#     frozen_credentials = credentials.get_frozen_credentials()
#     region = s3_client.meta.region_name
#     auth = botocore.auth.S3SigV4Auth(frozen_credentials, "s3", region)
#     auth.add_auth(request)

#     return request


# def generate_upload_policy(item):
#     """
#     Generate a S3 upload policy for a given item.
#     """

#     # Generate a unique key for the item
#     key = f"{item.key_base}/{item.filename}"

#     # Generate the policy
#     s3_client = default_storage.connection.meta.client
#     policy = s3_client.generate_presigned_post(
#         default_storage.bucket_name,
#         key,
#         Fields={"acl": "private"},
#         Conditions=[
#             {"acl": "private"},
#             ["content-length-range", 0, settings.ITEM_FILE_MAX_SIZE],
#         ],
#         ExpiresIn=settings.AWS_S3_UPLOAD_POLICY_EXPIRATION,
#     )

#     return policy
