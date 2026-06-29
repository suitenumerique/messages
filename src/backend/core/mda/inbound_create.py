"""Handles inbound email delivery logic: receiving messages and delivering to mailboxes."""

# pylint: disable=broad-exception-caught

import logging
import re
import uuid
from contextlib import contextmanager, nullcontext

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.utils import Error as DjangoDbError
from django.utils import timezone

from jmap_email import (
    JmapEmail,
    first_address_email,
    first_address_name,
    first_msgid,
    sent_at_to_datetime,
)

from core import enums, models
from core.ai.call_label import assign_label_to_thread
from core.ai.thread_summarizer import summarize_thread
from core.ai.utils import (
    get_messages_from_thread,
    is_ai_summary_enabled,
    is_auto_labels_enabled,
)
from core.mda.utils import thread_snippet
from core.services.importer.labels import (
    compute_labels_and_flags,
)

logger = logging.getLogger(__name__)

TOKEN_THRESHOLD_FOR_SUMMARY = 200  # Minimum token count to trigger summarization
MINIMUM_MESSAGES_FOR_SUMMARY = 3  # Minimum number of messages to trigger summarization

# Advisory-lock namespace for inbound delivery. Distinct ``classid`` from the
# blob-cohort locks (see core.services.tiered_storage) so the two never
# collide in Postgres' single global advisory-lock keyspace.
_ADVISORY_LOCK_CLASSID_INBOUND = 0x696E626E  # 'inbn' in ASCII


@contextmanager
def inbound_mailbox_lock(mailbox_id: uuid.UUID):
    """Serialize inbound message creation for one mailbox.

    Dedup (does a message with this ``mime_id`` already exist?) and
    thread-bucketing (does a thread already exist for this ``In-Reply-To`` /
    ``References``?) are both read-then-decide: two concurrent inbound
    deliveries to the same mailbox could each read "nothing there yet" and
    both create a row, yielding duplicate Messages or two parallel Threads
    with no reconcile path. Holding a per-mailbox Postgres advisory lock for
    the duration of the find-or-create makes that critical section
    cluster-wide serial.

    Must be called inside ``transaction.atomic()`` — ``pg_advisory_xact_lock``
    binds the lock to the current transaction and releases it on
    commit/rollback. The lock is held only across the (DB-only) find-or-create
    work; slow steps (spam scoring, AI summary/labels) run outside it.
    """
    # First 4 bytes of the mailbox UUID as a signed int32 — the two-arg
    # pg_advisory_xact_lock(classid, objid) form takes two int4s. A 2^-32
    # false-share collision is operationally invisible given the short,
    # DB-only critical section it guards.
    objid = int.from_bytes(mailbox_id.bytes[:4], byteorder="big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            [_ADVISORY_LOCK_CLASSID_INBOUND, objid],
        )
        yield


def _canonicalize_subject(subject: str | None) -> str:
    """Strip leading ``Re:`` / ``Fwd:`` (and i18n variants) for thread match."""
    return re.sub(
        r"^((re|fwd|fw|rep|tr|rép)\s*:\s+)+",
        "",
        (subject or "").lower(),
        flags=re.IGNORECASE,
    ).strip()


def find_thread_for_inbound_message(
    parsed_email: JmapEmail, mailbox: models.Mailbox
) -> models.Thread | None:
    """Attempt to find an existing thread for an inbound message.

    Follows JMAP spec recommendations:
    https://www.ietf.org/rfc/rfc8621.html#section-3
    """
    in_reply_to = first_msgid(parsed_email.get("inReplyTo"))
    references = parsed_email.get("references") or []

    all_referenced_ids = set(references)
    if in_reply_to:
        all_referenced_ids.add(in_reply_to)

    if not all_referenced_ids:
        return None  # No headers to match on

    # Find potential parent messages in the target mailbox based on references
    potential_parents = list(
        models.Message.objects.filter(
            mime_id__in=list(all_referenced_ids),
            thread__accesses__mailbox=mailbox,
        )
        .select_related("thread")
        .order_by("-created_at")  # Prefer newer matches if multiple found
    )

    if len(potential_parents) == 0:
        return None  # No matching messages found by ID in this mailbox

    # Strategy 1: Match by reference AND canonical subject
    incoming_subject_canonical = _canonicalize_subject(parsed_email.get("subject"))
    for parent in potential_parents:
        parent_subject_canonical = _canonicalize_subject(parent.subject)
        if incoming_subject_canonical == parent_subject_canonical:
            return parent.thread  # Found a match!

    # Strategy 2 (Fallback): If no subject match, return thread of the most recent parent message
    # The list is ordered by -sent_at, so the first element is the latest match.
    return None  # potential_parents.first().thread


def find_thread_for_import(
    parsed_email: JmapEmail, mailbox: models.Mailbox
) -> models.Thread | None:
    """
    During import, try to find an existing thread that contains messages
    with the same subject or referenced message IDs.
    """

    subject = parsed_email.get("subject", "")
    in_reply_to = first_msgid(parsed_email.get("inReplyTo"))
    references = parsed_email.get("references") or []

    # First try to find a thread by message IDs
    thread = _find_thread_by_message_ids(in_reply_to, references, mailbox)

    # If no thread found by message IDs, try by subject
    if not thread and subject:
        # Look for threads with similar subjects
        canonical_subject = _canonicalize_subject(subject)
        thread = models.Thread.objects.filter(
            subject__iregex=rf"^(re|fwd|fw|rep|tr|rép)\s*:\s*{re.escape(canonical_subject)}$",
            accesses__mailbox=mailbox,
        ).first()

    return thread


def _create_thread(parsed_email: JmapEmail, mailbox: models.Mailbox) -> models.Thread:
    """Create a new thread."""

    snippet = thread_snippet(
        parsed_email,
        fallback=parsed_email.get("subject") or "(No snippet available)",
    )

    # Truncate subject to 255 characters if it exceeds max_length
    thread_subject = parsed_email.get("subject")
    if thread_subject and len(thread_subject) > 255:
        thread_subject = thread_subject[:255]

    thread = models.Thread.objects.create(
        subject=thread_subject,
        snippet=snippet,
    )
    # Create a thread access for the sender mailbox
    models.ThreadAccess.objects.create(
        thread=thread,
        mailbox=mailbox,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )

    return thread


def _find_thread_by_message_ids(
    in_reply_to: str, references: list[str], mailbox: models.Mailbox
) -> models.Thread | None:
    """Find thread by message IDs (``inReplyTo`` and ``references``)."""
    if in_reply_to or references:
        thread = models.Thread.objects.filter(
            messages__mime_id__in=[in_reply_to] if in_reply_to else [],
            accesses__mailbox=mailbox,
        ).first()
        if not thread and references:
            thread = models.Thread.objects.filter(
                messages__mime_id__in=references,
                accesses__mailbox=mailbox,
            ).first()
        return thread
    return None


def _create_message_from_inbound(  # pylint: disable=too-many-arguments
    recipient_email: str,
    parsed_email: JmapEmail,
    raw_data: bytes,
    mailbox: models.Mailbox,
    is_import: bool = False,
    is_import_sender: bool = False,
    imap_labels: list[str] | None = None,
    imap_flags: list[str] | None = None,
    channel: models.Channel | None = None,
    is_spam: bool = False,
    is_outbound: bool = False,
) -> models.Message | None:
    """Create a message and thread from parsed email data.

    Used for inbound delivery, imports, and outbound submission.
    Returns the created Message on success, or None on failure.
    Callers that only need a boolean can check truthiness of the return value.

    When ``is_outbound`` is True:
    - ``is_sender`` is forced to True
    - No blob is created (the caller handles DKIM signing + blob via prepare_outbound_message)
    - AI features (summary, auto-labels) are skipped
    - The message is created as a draft (finalized later by prepare_outbound_message)

    Warning: messages imported here could be is_sender=True.
    """
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    message_flags = {}

    mime_id = first_msgid(parsed_email.get("messageId")) or None

    # Dedup, thread-bucketing and the message INSERT form one read-then-write
    # critical section. Serialize it per mailbox under a Postgres advisory lock
    # (held only across this DB-only work) so concurrent inbound deliveries to
    # the same mailbox cannot create duplicate Messages or split a conversation
    # into two parallel Threads. Imports are a single-writer backfill path and
    # skip the lock to avoid serializing bulk loads.
    lock_ctx = nullcontext() if is_import else inbound_mailbox_lock(mailbox.id)
    with transaction.atomic(), lock_ctx:
        # Recheck for an already-stored copy now that we hold the lock.
        # deliver_inbound_message dedups before queueing, but the async
        # processing path and concurrent deliveries can still reach here twice
        # for the same Message-ID; this makes creation idempotent per
        # (mailbox, mime_id).
        if not is_outbound and mime_id:
            existing_message = models.Message.objects.filter(
                mime_id=mime_id, thread__accesses__mailbox=mailbox
            ).first()
            if existing_message:
                logger.info(
                    "Duplicate inbound message %s (MIME ID: %s) in mailbox %s; "
                    "skipping create",
                    existing_message.id,
                    mime_id,
                    mailbox.id,
                )
                return existing_message

        # --- 3. Find or Create Thread --- #
        try:
            thread = None
            if is_import:
                thread = find_thread_for_import(parsed_email, mailbox)

            # If no thread found or not an import, use normal thread finding logic
            if not thread:
                thread = find_thread_for_inbound_message(parsed_email, mailbox)

            if not thread:
                thread = _create_thread(parsed_email, mailbox)

        except (DjangoDbError, ValidationError) as e:
            logger.error(
                "Failed to find or create thread for %s: %s", recipient_email, e
            )
            # Returning from inside the atomic block would commit any partial
            # writes (e.g. a thread without its message); roll back instead.
            transaction.set_rollback(True)
            return None  # Indicate failure
        except Exception as e:
            logger.exception(
                "Unexpected error finding/creating thread for %s: %s",
                recipient_email,
                e,
            )
            transaction.set_rollback(True)
            return None

        if is_import:
            # get labels from parsed_email
            labels, message_flags = compute_labels_and_flags(
                parsed_email, imap_labels, imap_flags
            )
            for label in labels:
                try:
                    label_obj, _ = models.Label.objects.get_or_create(
                        name=label, mailbox=mailbox
                    )
                    thread.labels.add(label_obj)
                except Exception as e:
                    logger.exception("Error creating label %s: %s", label, e)
                    continue

        # Apply labels from channel settings (e.g., widget channel tags)
        if channel and channel.settings:
            channel_tags = channel.settings.get("tags", [])
            for tag_id in channel_tags:
                try:
                    label_obj = models.Label.objects.get(id=tag_id, mailbox=mailbox)
                    thread.labels.add(label_obj)
                except models.Label.DoesNotExist:
                    logger.warning(
                        "Label %s not found for channel %s, skipping",
                        tag_id,
                        channel.id,
                    )
                except Exception as e:
                    logger.exception(
                        "Error adding label %s from channel: %s", tag_id, e
                    )

        # --- 4. Get or Create Sender Contact --- #
        sender_email = first_address_email(parsed_email.get("from"))
        sender_name = first_address_name(parsed_email.get("from"))

        if not sender_email:
            logger.warning(
                "Inbound message for %s missing 'From' email, using fallback.",
                recipient_email,
            )
            sender_email = (
                f"unknown-sender@{mailbox.domain.name}"  # Use recipient's domain
            )
            sender_name = sender_name or "Unknown Sender"

        try:
            # Validate sender_email format before saving
            models.Contact(email=sender_email).full_clean(
                exclude=["mailbox", "name"]
            )  # Validate email format

            sender_contact, created = models.Contact.objects.get_or_create(
                email=sender_email,
                mailbox=mailbox,  # Associate contact with the recipient mailbox
                defaults={
                    "name": sender_name or sender_email.split("@")[0],
                    "email": sender_email,  # Ensure correct casing is saved
                },
            )
            if created:
                logger.info(
                    "Created contact for sender %s in mailbox %s",
                    sender_email,
                    mailbox.id,
                )

        except ValidationError as e:
            logger.error(
                "Validation error for sender contact %s in mailbox %s: %s. Using fallback.",
                sender_email,
                mailbox.id,
                e,
            )
            # Fallback: Use a generic placeholder contact if validation fails
            sender_email = f"invalid-sender@{mailbox.domain.name}"
            sender_name = "Invalid Sender Address"
            sender_contact, _ = models.Contact.objects.get_or_create(
                email=sender_email,
                mailbox=mailbox,
                defaults={"name": sender_name, "email": sender_email},
            )
        except DjangoDbError as e:
            logger.error(
                "DB error getting/creating sender contact %s in mailbox %s: %s",
                sender_email,
                mailbox.id,
                e,
            )
            transaction.set_rollback(True)
            return None  # Indicate failure
        except Exception as e:
            logger.exception(
                "Unexpected error with sender contact %s in mailbox %s: %s",
                sender_email,
                mailbox.id,
                e,
            )
            transaction.set_rollback(True)
            return None

        # --- 5. Create Message --- #
        try:
            # Can we get a parent message for reference?
            # TODO: validate this doesn't create security issues
            parent_message = None
            parent_msg_id = first_msgid(parsed_email.get("inReplyTo"))
            if parent_msg_id:
                parent_message = models.Message.objects.filter(
                    mime_id=parent_msg_id, thread=thread
                ).first()

            # Truncate subject to 255 characters if it exceeds max_length
            subject = parsed_email.get("subject")
            if subject and len(subject) > 255:
                subject = subject[:255]

            is_sender = is_outbound or (is_import and is_import_sender)
            sent_at = sent_at_to_datetime(parsed_email.get("sentAt"))

            # The Blob INSERT and the Message INSERT must commit together
            # so the GC sweep never sees the Blob row without its
            # referencing FK on ``Message.blob``. Outbound messages have
            # no blob yet — ``prepare_outbound_message`` adds it later.
            with transaction.atomic():
                blob = None
                if not is_outbound:
                    blob = models.Blob.objects.create_blob(
                        content=raw_data,
                        content_type="message/rfc822",
                    )

                message = models.Message.objects.create(
                    thread=thread,
                    sender=sender_contact,
                    subject=subject,
                    blob=blob,
                    mime_id=first_msgid(parsed_email.get("messageId")) or None,
                    parent=parent_message,
                    sent_at=(None if is_outbound else (sent_at or timezone.now())),
                    is_draft=is_outbound,  # Outbound: draft until prepare_outbound_message finalizes
                    is_sender=is_sender,
                    is_trashed=False,
                    is_spam=is_spam,
                    has_attachments=len(parsed_email.get("attachments", [])) > 0,
                    channel=channel,
                )
            if is_import:
                # We need to set the created_at field to the date of the message
                # because the inbound message is not created at the same time as the message is received
                message.created_at = sent_at or timezone.now()
                # Extract flags handled via ThreadAccess (not Message fields)
                import_is_unread = message_flags.pop("is_unread", True)
                import_is_starred = message_flags.pop("_starred", False)

                for flag, value in message_flags.items():
                    if hasattr(message, flag):
                        setattr(message, flag, value)
                message.save(
                    update_fields=[
                        "created_at",
                        *message_flags.keys(),
                    ]
                )
                # Update ThreadAccess for read/starred state
                access = models.ThreadAccess.objects.filter(
                    thread=thread, mailbox=mailbox
                ).first()
                if access:
                    update_fields = []
                    # Sent messages are always considered read by the sender
                    if (is_sender or not import_is_unread) and (
                        access.read_at is None or message.created_at > access.read_at
                    ):
                        access.read_at = message.created_at
                        update_fields.append("read_at")
                    if import_is_starred and access.starred_at is None:
                        access.starred_at = message.created_at
                        update_fields.append("starred_at")
                    if update_fields:
                        access.save(update_fields=update_fields)
            elif is_sender:
                access = models.ThreadAccess.objects.filter(
                    thread=thread, mailbox=mailbox
                ).first()
                if access:
                    access.read_at = message.created_at
                    access.save(update_fields=["read_at"])
        except (DjangoDbError, ValidationError) as e:
            logger.error("Failed to create message in thread %s: %s", thread.id, e)
            transaction.set_rollback(True)
            return None  # Indicate failure
        except Exception as e:
            logger.exception(
                "Unexpected error creating message in thread %s: %s",
                thread.id,
                e,
            )
            transaction.set_rollback(True)
            return None

    # --- 6. Create Recipient Contacts and Links --- #
    # deduplicate recipients
    recipient_types_to_process = []
    for type_choice, type_name in [
        (models.MessageRecipientTypeChoices.TO, "to"),
        (models.MessageRecipientTypeChoices.CC, "cc"),
        (models.MessageRecipientTypeChoices.BCC, "bcc"),
    ]:
        recipients = list(
            {
                frozenset(recipient.items())
                for recipient in (parsed_email.get(type_name) or [])
            }
        )
        recipient_types_to_process.append(
            (type_choice, [dict(recipient) for recipient in recipients])
        )

    for recipient_type, recipients_list in recipient_types_to_process:
        for recipient_data in recipients_list:
            email = recipient_data.get("email")
            name = recipient_data.get("name")
            if not email:
                logger.warning(
                    "Skipping recipient with no email address for message %s.",
                    message.id,
                )
                continue

            try:
                models.Contact(email=email).full_clean(
                    exclude=["mailbox", "name"]
                )  # Validate
                recipient_contact, created = models.Contact.objects.get_or_create(
                    email=email,
                    mailbox=mailbox,  # Associate contact with the recipient mailbox
                    defaults={"name": name or email.split("@")[0], "email": email},
                )
                if created:
                    logger.info(
                        "Created contact for recipient %s in mailbox %s",
                        email,
                        mailbox.id,
                    )

                # Create the link between message and contact (use get_or_create to handle duplicates)
                defaults = {}
                if is_import and not message.is_draft:
                    defaults["delivery_status"] = (
                        enums.MessageDeliveryStatusChoices.SENT
                    )
                models.MessageRecipient.objects.get_or_create(
                    message=message,
                    contact=recipient_contact,
                    type=recipient_type,
                    defaults=defaults,
                )
            except ValidationError as e:
                logger.warning(
                    "Validation error creating recipient contact/link (%s) for message %s: %s",
                    email,
                    message.id,
                    e,
                )
                # Continue processing other recipients even if one fails validation
            except DjangoDbError as e:
                logger.error(
                    "DB error creating recipient contact/link (%s) for message %s: %s",
                    email,
                    message.id,
                    e,
                )
                # Potentially return False here if one recipient failure should stop all?
                # For now, log and continue.
            except Exception as e:
                logger.exception(
                    "Unexpected error with recipient contact/link %s for msg %s: %s",
                    email,
                    message.id,
                    e,
                )
                # Log and continue

    # --- 7. Process Attachments if present --- #
    # if parsed_email.get("attachments"):
    #    _process_attachments(message, parsed_email["attachments"], mailbox)

    # --- 8. Final Updates --- #
    try:
        # Update snippet using the new message's body if possible
        # (This assumes the subject was used for the initial snippet if body was empty)
        new_snippet = thread_snippet(
            parsed_email,
            fallback=parsed_email.get("subject", ""),
        )

        if new_snippet:
            thread.snippet = new_snippet
            thread.save(update_fields=["snippet"])

        # Do not trigger AI features on import, spam, or outbound
        if not is_import and not is_spam and not is_outbound:
            # Update summary if needed is ai is enabled
            if is_ai_summary_enabled():
                messages = get_messages_from_thread(thread)
                token_count = sum(message.get_tokens_count() for message in messages)

                # Only summarize if the thread has enough content (more than 200 tokens or at least 3 messages)
                if (
                    token_count >= TOKEN_THRESHOLD_FOR_SUMMARY
                    or len(messages) >= MINIMUM_MESSAGES_FOR_SUMMARY
                ):
                    new_summary = summarize_thread(thread)
                    if new_summary:
                        thread.summary = new_summary
                        thread.save(update_fields=["summary"])

            # Assign labels to the thread (skip if channel already applied tags)
            has_channel_tags = (
                channel and channel.settings and channel.settings.get("tags")
            )
            if is_auto_labels_enabled() and not has_channel_tags:
                assign_label_to_thread(thread, mailbox.id)

    except Exception as e:
        logger.exception(
            "Error updating thread %s after message delivery: %s",
            thread.id,
            e,
        )
        # Don't return False here, delivery was successful

    thread.update_stats()

    logger.info(
        "Successfully delivered message %s to mailbox %s (Thread: %s)",
        message.id,
        mailbox.id,
        thread.id,
    )
    return message  # Return created Message on success (truthy), None on failure


# def _process_attachments(
#     message: models.Message, attachment_data: list[Dict], mailbox: models.Mailbox
# ) -> None:
#     """
#     Process attachments found during email parsing.

#     Creates Blob records for each attachment and links them to the message.

#     Args:
#         message: The message object to link attachments to
#         attachment_data: List of attachment data dictionaries from parsing
#         mailbox: The mailbox that owns these attachments
#     """
#     for attachment_info in attachment_data:
#         try:
#             # Check if we have content to store
#             if "content" in attachment_info and attachment_info["content"]:
#                 # Create a blob for this attachment using the mailbox method
#                 content = attachment_info["content"]
#                 blob = mailbox.create_blob(
#                     content=content,
#                     content_type=attachment_info["type"],
#                 )

#                 # Create an attachment record linking to this blob
#                 attachment = models.Attachment.objects.create(
#                     name=attachment_info.get("name", "unnamed"),
#                     blob=blob,
#                     mailbox=mailbox,
#                 )

#                 # Link the attachment to the message
#                 message.attachments.add(attachment)
#         except Exception as e:
#             logger.exception("Error processing attachment: %s", e)
