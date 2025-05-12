"""Handles email delivery logic: receiving inbound and sending outbound messages."""
# pylint: disable=broad-exception-caught

import html
import logging
import re
import smtplib
import time
from typing import Any, Dict, Optional

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.utils import Error as DjangoDbError
from django.utils import timezone

from core import models
from core.mda.rfc5322 import compose_email
from core.mda.signing import sign_message_dkim

logger = logging.getLogger(__name__)

# Helper function to extract Message-IDs
MESSAGE_ID_RE = re.compile(r"<([^<>]+)>")


def check_local_recipient(
    email_address: str, create_if_missing: bool = False
) -> bool | models.Mailbox:
    """Check if a recipient email is locally deliverable."""

    is_deliverable = False

    try:
        local_part, domain_name = email_address.split("@", 1)
    except ValueError:
        return False  # Invalid format

    # For unit testing, we accept all emails
    if settings.MESSAGES_ACCEPT_ALL_EMAILS:
        is_deliverable = True
    # MESSAGES_TESTDOMAIN acts as a catch-all, if configured.
    elif settings.MESSAGES_TESTDOMAIN == domain_name:
        is_deliverable = True
    else:
        # Check if the email address exists in the database
        is_deliverable = models.Mailbox.objects.filter(
            local_part=local_part,
            domain__name=domain_name,
        ).exists()

    if not is_deliverable:
        return False

    if create_if_missing:
        # Create a new mailbox if it doesn't exist
        maildomain, _ = models.MailDomain.objects.get_or_create(name=domain_name)
        mailbox, _ = models.Mailbox.objects.get_or_create(
            local_part=local_part,
            domain=maildomain,
        )
        return mailbox

    return True


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


def deliver_inbound_message(
    recipient_email: str, parsed_email: Dict[str, Any], raw_data: bytes
) -> bool:  # Return True on success, False on failure
    """Deliver a parsed inbound email message to the correct mailbox and thread."""

    # --- 1. Find or Create Mailbox --- #
    try:
        mailbox = check_local_recipient(recipient_email, create_if_missing=True)
    except Exception as e:
        logger.exception("Error checking local recipient: %s", e)
        return False

    if not mailbox:
        logger.warning("Invalid recipient address: %s", recipient_email)
        return False

    # --- 2. Find or Create Thread --- #
    try:
        thread = find_thread_for_inbound_message(parsed_email, mailbox)
        if not thread:
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

            thread = models.Thread.objects.create(
                subject=parsed_email.get("subject", "(no subject)"),
                snippet=snippet,
                count_unread=1,
            )
            # Create a thread access for the sender mailbox
            models.ThreadAccess.objects.create(
                thread=thread,
                mailbox=mailbox,
                role=models.ThreadAccessRoleChoices.EDITOR,
            )
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

    # --- 3. Get or Create Sender Contact --- #
    logger.warning(parsed_email)
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
            email__iexact=sender_email,
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
            email__iexact=sender_email,
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

    # --- 4. Create Message --- #
    try:
        # Can we get a parent message for reference?
        # TODO: validate this doesn't create security issues
        parent_message = None
        if parsed_email.get("in_reply_to"):
            parent_message = models.Message.objects.filter(
                mime_id=parsed_email.get("in_reply_to"), thread=thread
            ).first()

        message = models.Message.objects.create(
            thread=thread,
            sender=sender_contact,
            subject=parsed_email.get("subject", "(no subject)"),
            raw_mime=raw_data,
            mime_id=parsed_email.get("messageId", parsed_email.get("message_id"))
            or None,
            parent=parent_message,
            sent_at=parsed_email.get("date") or timezone.now(),
            read_at=None,
            is_draft=False,
            is_sender=False,
            is_starred=False,
            is_trashed=False,
            is_unread=True,
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

    # --- 5. Create Recipient Contacts and Links --- #
    recipient_types_to_process = [
        (models.MessageRecipientTypeChoices.TO, parsed_email.get("to", [])),
        (models.MessageRecipientTypeChoices.CC, parsed_email.get("cc", [])),
        (models.MessageRecipientTypeChoices.BCC, parsed_email.get("bcc", [])),
    ]
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
                    email__iexact=email,
                    mailbox=mailbox,  # Associate contact with the recipient mailbox
                    defaults={"name": name or email.split("@")[0], "email": email},
                )
                if created:
                    logger.info(
                        "Created contact for recipient %s in mailbox %s",
                        email,
                        mailbox.id,
                    )

                # Create the link between message and contact
                models.MessageRecipient.objects.create(
                    message=message,
                    contact=recipient_contact,
                    type=recipient_type,
                )
            except ValidationError as e:
                logger.error(
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

    # --- 6. Final Updates (Optional) --- #
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


def prepare_outbound_message(
    message: models.Message, text_body: str, html_body: str
) -> tuple[bool, Optional[Dict[str, Any]]]:
    """Compose and sign an existing draft Message object before sending via SMTP."""

    # Get recipients from the MessageRecipient model
    recipients_by_type = {
        kind: [{"name": contact.name, "email": contact.email} for contact in contacts]
        for kind, contacts in message.get_all_recipient_contacts().items()
    }

    # TODO: Fetch MIME IDs of "references" from the thread
    # references = message.thread.messages.exclude(id=message.id).order_by("-created_at").all()

    # TODO: set the thread snippet?

    # Generate a MIME id
    message.mime_id = message.generate_mime_id()

    # Generate the MIME data dictionary
    mime_data = {
        "from": [
            {
                "name": message.sender.name,
                "email": message.sender.email,
            }
        ],
        "to": recipients_by_type.get(models.MessageRecipientTypeChoices.TO, []),
        "cc": recipients_by_type.get(models.MessageRecipientTypeChoices.CC, []),
        # BCC is not included in headers
        "subject": message.subject,
        "textBody": [{"content": text_body}] if text_body else [],
        "htmlBody": [{"content": html_body}] if html_body else [],
        "message_id": message.mime_id,
        "in_reply_to": message.parent.mime_id if message.parent else None,
    }

    # Assemble the raw mime message
    try:
        raw_mime = compose_email(
            mime_data,
            in_reply_to=mime_data["in_reply_to"],
            # TODO: Add References header logic
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to compose MIME for message %s: %s", message.id, e)
        return False, mime_data

    # Sign the message with DKIM
    dkim_signature_header: Optional[bytes] = sign_message_dkim(
        raw_mime_message=raw_mime, sender_email=message.sender.email
    )

    raw_mime_signed = raw_mime
    if dkim_signature_header:
        # Prepend the signature header
        raw_mime_signed = dkim_signature_header + b"\r\n" + raw_mime

    message.raw_mime = raw_mime_signed
    # message.is_draft = False
    # message.draft_body = None
    # message.created_at = timezone.now()
    message.updated_at = timezone.now()
    message.save(
        update_fields=["updated_at", "raw_mime", "mime_id"]
    )  # "is_draft", "draft_body", "created_at"

    return True, mime_data


def _mark_message_as_sent(message: models.Message, mta_sent: bool) -> bool:
    """Mark a message as sent and update its fields."""

    # TODO: move these 3 back to prepare_outbound_message when we have workers
    message.is_draft = False
    message.draft_body = None
    message.created_at = timezone.now()

    message.mta_sent = mta_sent
    message.sent_at = timezone.now()
    message.save(
        update_fields=["mta_sent", "sent_at", "is_draft", "draft_body", "created_at"]
    )

    message.thread.update_stats()


def send_message(
    message: models.Message, mime_data: Dict[str, Any], force_mta_out: bool = False
) -> Dict[str, bool]:
    """Send an existing Message, internally or externally."""

    # Include all recipients in the envelope, including BCC
    envelope_to = [
        contact.email
        for contacts in message.get_all_recipient_contacts().values()
        for contact in contacts
    ]

    successes = {}
    external_recipients = []
    for recipient_email in envelope_to:
        if (
            check_local_recipient(recipient_email, create_if_missing=True)
            and not force_mta_out
        ):
            successes[recipient_email] = deliver_inbound_message(
                recipient_email, mime_data, message.raw_mime
            )
        else:
            external_recipients.append(recipient_email)

    if len(external_recipients) > 0:
        # TODO: get success for each recipient
        all_success = send_outbound_message(external_recipients, message)
        for recipient_email in external_recipients:
            successes[recipient_email] = all_success

        if all_success:
            _mark_message_as_sent(message, True)
    else:
        _mark_message_as_sent(message, False)

    return successes


def send_outbound_message(recipient_emails: list[str], message: models.Message) -> bool:
    """Send an existing Message object via MTA out (SMTP)."""

    # Send via SMTP
    if not settings.MTA_OUT_HOST:
        logger.warning(
            "MTA_OUT_HOST is not set, skipping SMTP sending for %s", message.id
        )
        # Mark as sent for testing/dev purposes? Or handle differently?
        # For now, we fail the send if MTA isn't configured.
        return False

    smtp_host, smtp_port_str = settings.MTA_OUT_HOST.split(":")
    smtp_port = int(smtp_port_str)
    envelope_from = message.sender.email

    # Retry sending logic
    max_retries = 5
    for attempt in range(max_retries):
        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as client:
                client.ehlo()
                if settings.MTA_OUT_SMTP_USE_TLS:
                    client.starttls()
                    client.ehlo()  # Re-EHLO after STARTTLS

                # Authenticate if credentials provided
                if settings.MTA_OUT_SMTP_USERNAME and settings.MTA_OUT_SMTP_PASSWORD:
                    client.login(
                        settings.MTA_OUT_SMTP_USERNAME,
                        settings.MTA_OUT_SMTP_PASSWORD,
                    )

                smtp_response = client.sendmail(
                    envelope_from, recipient_emails, message.raw_mime
                )
                logger.info(
                    "Sent message %s via SMTP (attempt %d). Response: %s",
                    message.id,
                    attempt + 1,
                    smtp_response,
                )

                return True

        except smtplib.SMTPException as e:
            logger.error(
                "SMTP error sending message %s (attempt %d/%d): %s",
                message.id,
                attempt + 1,
                max_retries,
                e,
            )
            if attempt < max_retries - 1:
                time.sleep(2**attempt)  # Exponential backoff
            else:
                logger.error(
                    "Failed to send message %s after %d attempts.",
                    message.id,
                    max_retries,
                )
                return False  # All attempts failed
        except OSError as e:  # Catches socket errors etc.
            logger.error(
                "Socket/OS error sending message %s (attempt %d/%d): %s",
                message.id,
                attempt + 1,
                max_retries,
                e,
            )
            if attempt < max_retries - 1:
                time.sleep(2**attempt)
            else:
                logger.error(
                    "Failed to send message %s after %d attempts.",
                    message.id,
                    max_retries,
                )
                return False  # All attempts failed

    return False  # Should not be reached, but keeps linters happy
