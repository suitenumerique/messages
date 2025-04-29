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


def _find_thread_for_inbound_message(
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
    in_reply_to_ids = find_message_ids(parsed_email.get("in_reply_to"))
    references_ids = find_message_ids(parsed_email.get("references"))
    all_referenced_ids = in_reply_to_ids.union(references_ids)

    if not all_referenced_ids:
        return None  # No headers to match on

    # Prepare a list of IDs without angle brackets for DB query
    db_query_ids = list(all_referenced_ids)

    # Find potential parent messages in the target mailbox based on references
    potential_parents = (
        models.Message.objects.filter(
            # Query only for the bracketless IDs
            mime_id__in=db_query_ids,
            thread__mailbox=mailbox,
        )
        .select_related("thread")
        .order_by("-sent_at")  # Prefer newer matches if multiple found
    )

    if not potential_parents.exists():
        return None  # No matching messages found by ID in this mailbox

    # Strategy 1: Match by reference AND canonical subject
    incoming_subject_canonical = canonicalize_subject(parsed_email.get("subject"))
    for parent in potential_parents:
        parent_subject_canonical = canonicalize_subject(parent.subject)
        if incoming_subject_canonical == parent_subject_canonical:
            return parent.thread  # Found a match!

    # Strategy 2 (Fallback): If no subject match, return thread of the most recent parent message
    # The list is ordered by -sent_at, so the first element is the latest match.
    return potential_parents.first().thread


def deliver_inbound_message(
    recipient_email: str, parsed_email: Dict[str, Any], raw_data: bytes
) -> bool:  # Return True on success, False on failure
    """Deliver a parsed inbound email message to the correct mailbox and thread."""

    # --- 1. Find or Create Mailbox --- #
    if "@" not in recipient_email:
        logger.warning("Invalid recipient address (no domain): %s", recipient_email)
        return False  # Indicate failure
    local_part, domain_name = recipient_email.split("@", 1)

    try:
        mailbox = models.Mailbox.objects.select_related("domain").get(
            local_part__iexact=local_part, domain__name__iexact=domain_name
        )
    except models.Mailbox.DoesNotExist:
        if (
            settings.MESSAGES_ACCEPT_ALL_EMAILS
            or domain_name.lower() == settings.MESSAGES_TESTDOMAIN.lower()
        ):
            try:
                # Use get_or_create for domain as well
                maildomain, domain_created = models.MailDomain.objects.get_or_create(
                    name__iexact=domain_name,
                    defaults={"name": domain_name},  # Save with original casing
                )
                if domain_created:
                    logger.info("Auto-created mail domain %s", domain_name)

                # Create the mailbox
                mailbox = models.Mailbox.objects.create(
                    local_part=local_part, domain=maildomain
                )
                logger.info("Auto-created mailbox for %s", recipient_email)
            except (DjangoDbError, ValidationError) as e:
                logger.error(
                    "Failed to auto-create mailbox/domain for %s: %s",
                    recipient_email,
                    e,
                )
                return False  # Indicate failure
        else:
            logger.warning(
                "Mailbox not found for delivery and auto-creation disabled/not applicable: %s",
                recipient_email,
            )
            return False  # Indicate failure
    except Exception as e:
        logger.exception(
            f"Unexpected error finding/creating mailbox for {recipient_email}: {e}"
        )
        return False

    # --- 2. Find or Create Thread --- #
    try:
        thread = _find_thread_for_inbound_message(parsed_email, mailbox)
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
                mailbox=mailbox,
                snippet=snippet,
                is_read=False,
            )
    except (DjangoDbError, ValidationError) as e:
        logger.error("Failed to find or create thread for %s: %s", recipient_email, e)
        return False  # Indicate failure
    except Exception as e:
        logger.exception(
            f"Unexpected error finding/creating thread for {recipient_email}: {e}"
        )
        return False

    # --- 3. Get or Create Sender Contact --- #
    sender_info_list = parsed_email.get("from", [])
    sender_email = None
    sender_name = None
    if sender_info_list:
        sender_info = sender_info_list[0]
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
                "name": sender_name or sender_email[:100],
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
            f"Unexpected error with sender contact {sender_email} in mailbox {mailbox.id}: {e}"
        )
        return False

    # --- 4. Create Message --- #
    try:
        message = models.Message.objects.create(
            thread=thread,
            sender=sender_contact,
            subject=parsed_email.get("subject", "(no subject)"),
            raw_mime=raw_data,
            mime_id=parsed_email.get("message_id") or None,
            sent_at=parsed_email.get("date") or timezone.now(),
            read_at=None,
        )
    except (DjangoDbError, ValidationError) as e:
        logger.error("Failed to create message in thread %s: %s", thread.id, e)
        return False  # Indicate failure
    except Exception as e:
        logger.exception(
            f"Unexpected error creating message in thread {thread.id}: {e}"
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
                    defaults={"name": name or email[:100], "email": email},
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
                    f"Unexpected error with recipient contact/link {email} for msg {message.id}: {e}"
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
            new_snippet = "(No snippet available)"

        thread.snippet = new_snippet
        thread.is_read = False  # Ensure thread is marked unread
        # updated_at updates automatically
        thread.save(update_fields=["snippet", "is_read"])
    except Exception as e:
        logger.exception(
            f"Error updating thread {thread.id} after message delivery: {e}"
        )
        # Don't return False here, delivery was successful

    logger.info(
        "Successfully delivered message %s to mailbox %s (Thread: %s)",
        message.id,
        mailbox.id,
        thread.id,
    )
    return True  # Indicate success


def prepare_outbound_message(
    message: models.Message, text_body: str, html_body: str
) -> bool:
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
        "messageId": message.mime_id,
    }

    # Assemble the raw mime message
    try:
        raw_mime = compose_email(
            mime_data,
            in_reply_to=message.parent.mime_id if message.parent else None,
            # TODO: Add References header logic
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to compose MIME for message %s: %s", message.id, e)
        return False

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

    return True


def _mark_message_as_sent(message: models.Message) -> bool:
    """Mark a message as sent and update its fields."""

    # TODO: move these 3 back to prepare_outbound_message when we have workers
    message.is_draft = False
    message.draft_body = None
    message.created_at = timezone.now()

    message.mta_sent = True
    message.sent_at = timezone.now()
    message.save(
        update_fields=["mta_sent", "sent_at", "is_draft", "draft_body", "created_at"]
    )


def send_outbound_message(message: models.Message) -> bool:
    """Compose, sign, and send an existing Message object via SMTP."""

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
    # Include all recipients in the envelope as a flat list, including BCC
    envelope_to = [
        contact.email
        for contacts in message.get_all_recipient_contacts().values()
        for contact in contacts
    ]
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
                    envelope_from, envelope_to, message.raw_mime
                )
                logger.info(
                    "Sent message %s via SMTP (attempt %d). Response: %s",
                    message.id,
                    attempt + 1,
                    smtp_response,
                )

                _mark_message_as_sent(message)

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
