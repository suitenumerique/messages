"""Handles inbound email delivery logic: receiving messages and delivering to mailboxes."""

# pylint: disable=broad-exception-caught

import html
import logging
import re
from typing import Any, Dict, List, Optional

from django.core.exceptions import ValidationError
from django.db.utils import Error as DjangoDbError
from django.utils import timezone

from core import enums, models
from core.ai.call_label import assign_label_to_thread
from core.ai.thread_summarizer import summarize_thread
from core.ai.utils import (
    get_messages_from_thread,
    is_ai_summary_enabled,
    is_auto_labels_enabled,
)
from core.services.importer.labels import (
    compute_labels_and_flags,
)

logger = logging.getLogger(__name__)

# Helper function to extract Message-IDs
MESSAGE_ID_RE = re.compile(r"<([^<>]+)>")

TOKEN_THRESHOLD_FOR_SUMMARY = 200  # Minimum token count to trigger summarization
MINIMUM_MESSAGES_FOR_SUMMARY = 3  # Minimum number of messages to trigger summarization


def find_thread_for_inbound_message(
    parsed_email: Dict[str, Any], mailbox: models.Mailbox
) -> Optional[models.Thread]:
    """Attempt to find an existing thread for an inbound message.

    Follows JMAP spec recommendations:
    https://www.ietf.org/rfc/rfc8621.html#section-3
    """

    def find_message_ids(txt):
        # Extract all unique message IDs from a header string
        return set(MESSAGE_ID_RE.findall(txt or ""))

    def canonicalize_subject(subject):
        return re.sub(
            r"^((re|fwd|fw|rep|tr|rép)\s*:\s+)+",
            "",
            subject.lower(),
            flags=re.IGNORECASE,
        ).strip()

    # --- Logic --- #
    in_reply_to_ids = (
        {parsed_email.get("in_reply_to")} if parsed_email.get("in_reply_to") else set()
    )
    references_ids = find_message_ids(parsed_email.get("headers", {}).get("references"))
    all_referenced_ids = in_reply_to_ids.union(references_ids)

    # logger.info("All referenced IDs: %s %s", all_referenced_ids, parsed_email)

    if not all_referenced_ids:
        return None  # No headers to match on

    # Prepare a list of IDs without angle brackets for DB query
    db_query_ids = list(all_referenced_ids)

    # Find potential parent messages in the target mailbox based on references
    potential_parents = list(
        models.Message.objects.filter(
            # Query only for the bracketless IDs
            mime_id__in=db_query_ids,
            thread__accesses__mailbox=mailbox,
        )
        .select_related("thread")
        .order_by("-created_at")  # Prefer newer matches if multiple found
    )

    # logger.info("Potential parents: %s", potential_parents)

    if len(potential_parents) == 0:
        return None  # No matching messages found by ID in this mailbox

    # Strategy 1: Match by reference AND canonical subject
    incoming_subject_canonical = canonicalize_subject(parsed_email.get("subject"))
    for parent in potential_parents:
        parent_subject_canonical = canonicalize_subject(parent.subject)
        if incoming_subject_canonical == parent_subject_canonical:
            return parent.thread  # Found a match!

    # Strategy 2 (Fallback): If no subject match, return thread of the most recent parent message
    # The list is ordered by -sent_at, so the first element is the latest match.
    return None  # potential_parents.first().thread


def find_thread_for_import(
    parsed_email: Dict[str, Any], mailbox: models.Mailbox
) -> Optional[models.Thread]:
    """
    During import, try to find an existing thread that contains messages
    with the same subject or referenced message IDs.
    """

    subject = parsed_email.get("subject", "")
    in_reply_to = parsed_email.get("in_reply_to")
    references = parsed_email.get("headers", {}).get("references", "")

    # First try to find a thread by message IDs
    thread = _find_thread_by_message_ids(in_reply_to, references, mailbox)

    # If no thread found by message IDs, try by subject
    if not thread and subject:
        # Look for threads with similar subjects
        canonical_subject = re.sub(
            r"^((re|fwd|fw|rep|tr|rép)\s*:\s+)+",
            "",
            subject.lower(),
            flags=re.IGNORECASE,
        ).strip()
        thread = models.Thread.objects.filter(
            subject__iregex=rf"^(re|fwd|fw|rep|tr|rép)\s*:\s*{re.escape(canonical_subject)}$",
            accesses__mailbox=mailbox,
        ).first()

    return thread


def _create_thread(
    parsed_email: Dict[str, Any], mailbox: models.Mailbox
) -> models.Thread:
    """Create a new thread."""

    snippet = ""
    if text_body := parsed_email.get("textBody"):
        snippet = text_body[0].get("content", "")[:140]
    elif html_body := parsed_email.get("htmlBody"):
        html_content = html_body[0].get("content", "")
        clean_text = re.sub("<[^>]+>", " ", html_content)
        snippet = " ".join(html.unescape(clean_text).strip().split())[:140]
    # Fallback to subject if no body content
    elif subject_val := parsed_email.get("subject"):
        snippet = subject_val[:140]
    else:
        snippet = "(No snippet available)"  # Absolute fallback

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
    in_reply_to: str, references: str, mailbox: models.Mailbox
) -> Optional[models.Thread]:
    """Find thread by message IDs (in_reply_to and references)."""
    # First try to find a thread by message IDs
    if in_reply_to or references:
        thread = models.Thread.objects.filter(
            messages__mime_id__in=[in_reply_to] if in_reply_to else [],
            accesses__mailbox=mailbox,
        ).first()
        if not thread and references:
            # Extract message IDs from references
            ref_ids = MESSAGE_ID_RE.findall(references)
            if ref_ids:
                thread = models.Thread.objects.filter(
                    messages__mime_id__in=ref_ids,
                    accesses__mailbox=mailbox,
                ).first()
        return thread
    return None


def _create_message_from_inbound(
    recipient_email: str,
    parsed_email: Dict[str, Any],
    raw_data: bytes,
    mailbox: models.Mailbox,
    is_import: bool = False,
    is_import_sender: bool = False,
    imap_labels: Optional[List[str]] = None,
    imap_flags: Optional[List[str]] = None,
    channel: Optional[models.Channel] = None,
    is_spam: bool = False,
) -> bool:
    """Create a message and thread from inbound message data.

    Warning: messages imported here could be is_sender=True.

    This method continues the logic of deliver_inbound_message, potentially asynchronously.

    TODO: continue splitting this into smaller methods.
    """
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    message_flags = {}

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
        logger.error("Failed to find or create thread for %s: %s", recipient_email, e)
        return False  # Indicate failure
    except Exception as e:
        logger.exception(
            "Unexpected error finding/creating thread for %s: %s",
            recipient_email,
            e,
        )
        return False

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

    # --- 4. Get or Create Sender Contact --- #
    sender_info = parsed_email.get("from", {})
    sender_email = sender_info.get("email")
    sender_name = sender_info.get("name")

    if not sender_email:
        logger.warning(
            "Inbound message for %s missing 'From' email, using fallback.",
            recipient_email,
        )
        sender_email = f"unknown-sender@{mailbox.domain.name}"  # Use recipient's domain
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
                "Created contact for sender %s in mailbox %s", sender_email, mailbox.id
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
        return False  # Indicate failure
    except Exception as e:
        logger.exception(
            "Unexpected error with sender contact %s in mailbox %s: %s",
            sender_email,
            mailbox.id,
            e,
        )
        return False

    # --- 5. Create Message --- #
    try:
        # Can we get a parent message for reference?
        # TODO: validate this doesn't create security issues
        parent_message = None
        if parsed_email.get("in_reply_to"):
            parent_message = models.Message.objects.filter(
                mime_id=parsed_email.get("in_reply_to"), thread=thread
            ).first()

        blob = mailbox.create_blob(
            content=raw_data,
            content_type="message/rfc822",
        )

        # Truncate subject to 255 characters if it exceeds max_length
        subject = parsed_email.get("subject")
        if subject and len(subject) > 255:
            subject = subject[:255]

        is_sender = (is_import and is_import_sender) or (
            sender_email == recipient_email
        )

        message = models.Message.objects.create(
            thread=thread,
            sender=sender_contact,
            subject=subject,
            blob=blob,
            mime_id=parsed_email.get("messageId", parsed_email.get("message_id"))
            or None,
            parent=parent_message,
            sent_at=parsed_email.get("date") or timezone.now(),
            read_at=None,
            is_draft=False,
            is_sender=is_sender,
            is_starred=False,
            is_trashed=False,
            is_unread=not is_sender,
            is_spam=is_spam,
            has_attachments=len(parsed_email.get("attachments", [])) > 0,
            channel=channel,
        )
        if is_import:
            # We need to set the created_at field to the date of the message
            # because the inbound message is not created at the same time as the message is received
            message.created_at = parsed_email.get("date") or timezone.now()
            for flag, value in message_flags.items():
                if hasattr(message, flag):
                    setattr(message, flag, value)
            message.save(
                update_fields=[
                    "created_at",
                    *message_flags.keys(),
                ]
            )
    except (DjangoDbError, ValidationError) as e:
        logger.error("Failed to create message in thread %s: %s", thread.id, e)
        return False  # Indicate failure
    except Exception as e:
        logger.exception(
            "Unexpected error creating message in thread %s: %s",
            thread.id,
            e,
        )
        return False

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
                for recipient in parsed_email.get(type_name, [])
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
                    defaults["delivery_status"] = enums.MessageDeliveryStatusChoices.SENT
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
        new_snippet = ""
        if text_body := parsed_email.get("textBody"):
            new_snippet = text_body[0].get("content", "")[:140]
        elif html_body := parsed_email.get("htmlBody"):
            html_content = html_body[0].get("content", "")
            clean_text = re.sub("<[^>]+>", " ", html_content)
            new_snippet = " ".join(html.unescape(clean_text).strip().split())[:140]
        elif subject_val := parsed_email.get("subject"):  # Fallback to subject
            new_snippet = subject_val[:140]
        else:
            new_snippet = ""

        if new_snippet:
            thread.snippet = new_snippet
            thread.save(update_fields=["snippet"])

        # Do not trigger AI features on import or spam
        if not is_import and not is_spam:
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

            # Assign labels to the thread
            if is_auto_labels_enabled():
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
    return True  # Indicate success


# def _process_attachments(
#     message: models.Message, attachment_data: List[Dict], mailbox: models.Mailbox
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
