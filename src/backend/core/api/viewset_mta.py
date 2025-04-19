"""DRF Views for MTA endpoints"""

import hashlib
import logging
import os
from datetime import datetime
from datetime import timezone as dt_timezone

from django.conf import settings
from django.contrib.auth import get_user_model

import jwt
import rest_framework as drf
from rest_framework import authentication, status, viewsets
from rest_framework.permissions import IsAuthenticated

from core import models
from core.formats.rfc5322 import EmailParseError, parse_email_message

logger = logging.getLogger(__name__)

User = get_user_model()

# Check if we should accept all emails (for e2e testing)
ACCEPT_ALL_EMAILS = os.environ.get("ACCEPT_ALL_EMAILS", "").lower() == "true"


class MTAJWTAuthentication(authentication.BaseAuthentication):
    """
    Custom authentication for MTA endpoints using JWT tokens with email hash validation.
    Returns None or (user, auth)
    """

    def authenticate(self, request):
        # Get the auth header
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return None

        try:
            # Extract and validate JWT
            jwt_token = auth_header.split(" ")[1]
            payload = jwt.decode(
                jwt_token, settings.MDA_API_SECRET, algorithms=["HS256"]
            )

            if not payload.get("exp"):
                raise jwt.InvalidTokenError("Missing expiration time")

            # Validate email hash if there's a body
            if request.body:
                body_hash = hashlib.sha256(request.body).hexdigest()
                if body_hash != payload["body_hash"]:
                    raise jwt.InvalidTokenError("Invalid email hash")

            service_account = User()
            return (service_account, payload)

        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError) as e:
            raise drf.exceptions.AuthenticationFailed(str(e)) from e
        except (IndexError, KeyError):
            return None

    def authenticate_header(self, request):
        """Return the header to be used in the WWW-Authenticate response header."""
        return 'Bearer realm="MTA"'


class MTAViewSet(viewsets.GenericViewSet):
    """ViewSet for MTA-related endpoints"""

    permission_classes = [IsAuthenticated]
    authentication_classes = [MTAJWTAuthentication]

    @drf.decorators.action(
        detail=False,
        methods=["post"],
        url_path="check-recipients",
        parser_classes=[drf.parsers.JSONParser],
    )
    def check_recipients(self, request):
        """Handle incoming email from MTA"""

        # Get a list of email addresses from the request body
        email_addresses = request.data.get("addresses")
        if not email_addresses or not isinstance(email_addresses, list):
            return drf.response.Response(
                {"detail": "Missing addresses"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Check if each address exists
        ret = {}
        for email_address in email_addresses:
            ret[email_address] = True  # For now everything exists

        return drf.response.Response(ret)

    @drf.decorators.action(
        detail=False,
        methods=["post"],
        url_path="inbound-email",
        parser_classes=[drf.parsers.BaseParser],
    )
    def inbound_email(self, request):
        """Handle incoming email from MTA"""

        # Validate content type
        if request.content_type != "message/rfc822":
            return drf.response.Response(
                {"detail": "Content-Type must be message/rfc822"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # The JWT payload is now available in request.auth
        mta_metadata = request.auth
        if not mta_metadata:
            return drf.response.Response(
                {"detail": "Valid authorization required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        raw_data = request.body or ""

        logger.info(
            "Raw email received: %d bytes for %s",
            len(raw_data),
            mta_metadata["original_recipients"][0:4],
        )

        # Parse the email message using our centralized parser
        try:
            parsed_email = parse_email_message(raw_data)
            logger.debug(
                "Email parsed successfully: subject=%s", parsed_email["subject"]
            )
        except EmailParseError as e:
            logger.error("Failed to parse email: %s", str(e))
            return drf.response.Response(
                {"status": "error", "detail": "Failed to parse email"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # We may have received a single POST but for multiple recipients.
        # Every one of them should be treated as a separate message.
        for recipient in mta_metadata["original_recipients"]:
            try:
                self._deliver_message(recipient, parsed_email, raw_data)
            except Exception as e:  # noqa: BLE001 pylint: disable=broad-exception-caught
                logger.error("Error creating message: %s", e)
                return drf.response.Response(
                    {"status": "error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

        return drf.response.Response({"status": "ok"})

    def _deliver_message(self, recipient, parsed_email, raw_data):
        """Deliver a message to the recipients"""

        # Get domain from recipient
        if "@" not in recipient:
            logger.warning(f"Invalid recipient address (no domain): {recipient}")
            return

        local_part, domain_name = recipient.split("@", 1)

        # Find the maildomain for this recipient's domain
        maildomain = models.MailDomain.objects.filter(name=domain_name).first()

        # In test mode, auto-create domain if it doesn't exist
        if ACCEPT_ALL_EMAILS and not maildomain:
            logger.info(f"ACCEPT_ALL_EMAILS: Creating new mail domain: {domain_name}")
            maildomain = models.MailDomain.objects.create(name=domain_name)

        if not maildomain:
            logger.warning(f"Mail domain '{domain_name}' not found.")
            return

        # Create the mailbox if it doesn't exist.
        # In normal mode, we'll check in the Regie first if we are supposed
        # to manage it. In test mode, we auto-create all mailboxes.
        mailbox = models.Mailbox.objects.filter(
            local_part=local_part,
            domain=maildomain,
        ).first()

        if not mailbox:
            mailbox = models.Mailbox.objects.create(
                local_part=local_part,
                domain=maildomain,
            )

        # Find if there's already a thread with the same subject.
        thread = models.Thread.objects.filter(
            subject=parsed_email["subject"],
            mailbox=mailbox,
        ).first()

        if not thread:
            # Create a new thread with a snippet from the body text
            snippet = parsed_email.get("body_text", "")[:140]
            logger.info(
                f"Creating new Thread: subject='{parsed_email['subject']}', mailbox_id='{mailbox.id}'"
            )
            thread = models.Thread.objects.create(
                subject=parsed_email["subject"],
                mailbox=mailbox,
                snippet=snippet,
            )
        else:
            logger.info(f"Found existing Thread: ID='{thread.id}'")

        sender_email = parsed_email.get("from", {}).get("email")
        sender_name = parsed_email.get("from", {}).get("name")
        logger.info(
            f"Getting/Creating sender Contact: email='{sender_email}', name='{sender_name}'"
        )

        try:
            # Get or create the sender contact
            sender_contact, created = models.Contact.objects.get_or_create(
                email=sender_email,
                defaults={"name": sender_name or sender_email},
            )
            logger.info(
                f"Sender Contact: ID='{sender_contact.id}', Created='{created}'"
            )
        except Exception as e:
            logger.error("Error creating sender contact: %s", e, exc_info=True)
            sender_contact, _ = models.Contact.objects.get_or_create(
                email="unknown@unknown.com",
                defaults={"name": "Unknown sender"},
            )
            logger.warning("Fell back to 'Unknown sender' contact.")

        # --- Prepare fields for Message model ---
        subject = parsed_email.get("subject", "")
        raw_mime = parsed_email.get("raw_mime", "")
        received_at = parsed_email.get("date", datetime.now(dt_timezone.utc))

        # Get text/html content directly from the parsed JMAP structure
        body_text_parsed = (
            parsed_email["textBody"][0]["content"]
            if parsed_email.get("textBody")
            else ""
        )
        body_html_parsed = (
            parsed_email["htmlBody"][0]["content"]
            if parsed_email.get("htmlBody")
            else ""
        )

        # Align with test expectations for fallbacks
        final_body_text = body_text_parsed
        final_body_html = body_html_parsed

        if not body_text_parsed and body_html_parsed:
            final_body_text = body_html_parsed
        elif not body_html_parsed and body_text_parsed:
            final_body_html = body_text_parsed

        # Ensure both are at least empty strings if neither was present
        final_body_text = final_body_text or ""
        final_body_html = final_body_html or ""

        # Create a message
        logger.info(
            f"Creating Message for Thread ID='{thread.id}', Sender ID='{sender_contact.id}'"
        )
        message = models.Message.objects.create(
            thread=thread,
            sender=sender_contact,
            subject=subject,
            raw_mime=raw_mime,
            body_html=final_body_html,
            body_text=final_body_text,
            received_at=received_at,
            is_read=False,
            mta_sent=False,
        )
        logger.info(f"Message created: ID='{message.id}'")

        # Create recipients
        for recipient_type, recipients_list in [
            (models.MessageRecipientTypeChoices.TO, parsed_email["to"]),
            (models.MessageRecipientTypeChoices.CC, parsed_email["cc"]),
            (models.MessageRecipientTypeChoices.BCC, parsed_email["bcc"]),
        ]:
            for recipient_data in recipients_list:
                try:
                    recipient_contact, _ = models.Contact.objects.get_or_create(
                        email=recipient_data["email"],
                        defaults={
                            "name": recipient_data["name"] or recipient_data["email"]
                        },
                    )

                    models.MessageRecipient.objects.create(
                        message=message,
                        contact=recipient_contact,
                        type=recipient_type,
                    )
                except Exception as e:  # noqa: BLE001 pylint: disable=broad-exception-caught
                    logger.error("Error creating recipient: %s", e)
                    continue
