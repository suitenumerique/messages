"""DRF Views for MTA endpoints"""

import email
import hashlib
import logging

from django.conf import settings
from django.contrib.auth import get_user_model

import jwt
import rest_framework as drf
from rest_framework import authentication, status, viewsets
from rest_framework.permissions import IsAuthenticated

from core import models

logger = logging.getLogger(__name__)

User = get_user_model()


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

        # Parse the email message
        email_message = email.message_from_bytes(raw_data)

        # TODO: move non-mail-specific logic somewhere else as this is not the only place
        # where we'll receive "messages".

        # We may have received a single POST but for multiple recipients.
        # Every one of them should be treated as a separate message.
        # TODO: wrap all this in a transaction? with a lock?
        # Walk mime parts and display their metadata

        # Extract body parts properly
        body_html = None
        body_text = None

        for part in email_message.walk():
            content_type = part.get_content_type()
            # Skip multipart containers
            if content_type.startswith("multipart/"):
                continue

            if content_type == "text/plain" and body_text is None:
                payload = part.get_payload(decode=True)
                if payload:
                    body_text = payload.decode("utf-8", errors="replace")
            elif content_type == "text/html" and body_html is None:
                payload = part.get_payload(decode=True)
                if payload:
                    body_html = payload.decode("utf-8", errors="replace")

        # Set defaults if parts weren't found
        if body_text is None:
            body_text = body_html or ""
        if body_html is None:
            body_html = body_text or ""

        for recipient in mta_metadata["original_recipients"]:
            try:
                self._deliver_message(
                    recipient, email_message, (body_text, body_html, raw_data)
                )
            except Exception as e:  # noqa: BLE001 pylint: disable=broad-exception-caught
                logger.error("Error creating message: %s", e)
                return drf.response.Response(
                    {"status": "error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

        return drf.response.Response({"status": "ok"})

    def _deliver_message(self, recipient, email_message, parsed):
        """Deliver a message to the recipients"""

        body_text, body_html, raw_data = parsed

        # Find the maildomain for this recipient's domain
        # For now we don't create it if it doesn't exist, but
        # in the future we'll call the Regie and might do it on demand.
        maildomain = models.MailDomain.objects.filter(
            name=recipient.split("@")[1]
        ).first()

        if not maildomain:
            # Silently ignore this recipient for now
            return

        # Create the mailbox if it doesn't exist.
        # TODO: in the future we'll check in the Regie first if we are supposed
        # to manage it. For now we assume all local_parts exist and are managed
        # by us in a maildomain.
        mailbox, _ = models.Mailbox.objects.get_or_create(
            local_part=recipient.split("@")[0],
            domain=maildomain,
        )

        # Find if there's already a thread with the same subject.
        # TODO: many more conditions to check here.
        thread = models.Thread.objects.filter(
            subject=email_message["Subject"],
            mailbox=mailbox,
        ).first()

        if not thread:
            snippet = body_text[:140]
            thread = models.Thread.objects.create(
                subject=email_message["Subject"],
                mailbox=mailbox,
                snippet=snippet,
            )

        logger.info("Creating FROM contact %s", email_message["From"])

        try:
            # Get or create the sender contact
            sender_contact, _ = models.Contact.objects.get_or_create(
                email=email_message["From"],
                defaults={"name": email_message["From"]},
            )
        except Exception as e:  # noqa: BLE001 pylint: disable=broad-exception-caught
            logger.error("Error creating sender contact: %s", e)
            sender_contact, _ = models.Contact.objects.get_or_create(
                email="unknown@unknown.com",
                defaults={"name": "Unknown sender"},
            )

        # Create a message
        message = models.Message.objects.create(
            thread=thread,
            sender=sender_contact,
            subject=email_message.get("Subject") or "No subject",
            raw_mime=raw_data,
            body_html=body_html,
            body_text=body_text,
            sent_at=email_message.get("Date"),
            is_read=False,
            mta_sent=False,
        )

        # Get or create each of the recipients
        logger.info("Creating recipients contacts %s", email_message["To"])
        recipients = []
        for rcpnt in email_message["To"].split(","):
            try:
                recipient_contact, _ = models.Contact.objects.get_or_create(
                    email=rcpnt.strip(),
                    defaults={"name": rcpnt.strip()},
                )
            except Exception as e:  # noqa: BLE001 pylint: disable=broad-exception-caught
                logger.error("Error creating recipient contact: %s", e)
                continue
            recipients.append(recipient_contact)

        # Create a message recipient for each recipient
        for rcpnt in recipients:
            models.MessageRecipient.objects.create(
                message=message,
                contact=rcpnt,
                type=models.MessageRecipientTypeChoices.TO,
            )
