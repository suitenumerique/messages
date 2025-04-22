"""DRF Views for MTA endpoints"""

import hashlib
import html
import logging
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import Error as DjangoDbError
from django.forms import ValidationError
from django.utils import timezone

import jwt
import rest_framework as drf
from rest_framework import authentication, status, viewsets
from rest_framework.permissions import IsAuthenticated

from core import models
from core.formats.rfc5322 import EmailParseError, parse_email_message

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
            # For unit testing, we accept all emails
            if settings.MESSAGES_ACCEPT_ALL_EMAILS:
                ret[email_address] = True
                continue

            # MESSAGES_TESTDOMAIN acts as a catch-all, if configured.
            local_part, domain_name = email_address.split("@")
            if settings.MESSAGES_TESTDOMAIN == domain_name:
                ret[email_address] = True
                continue

            # Check if the email address exists in the database
            ret[email_address] = models.Mailbox.objects.filter(
                local_part=local_part,
                domain__name=domain_name,
            ).exists()

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
        try:
            parsed_email = parse_email_message(raw_data)
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

        if "@" not in recipient:
            logger.warning("Invalid recipient address (no domain): %s", recipient)
            return
        local_part, domain_name = recipient.split("@", 1)

        maildomain = models.MailDomain.objects.filter(name=domain_name).first()
        if not maildomain:
            if settings.MESSAGES_ACCEPT_ALL_EMAILS:
                maildomain = models.MailDomain.objects.create(name=domain_name)
            else:
                logger.warning("Mail domain %s not found.", domain_name)
                return

        mailbox = models.Mailbox.objects.filter(
            local_part=local_part, domain=maildomain
        ).first()
        if not mailbox:
            if (
                settings.MESSAGES_ACCEPT_ALL_EMAILS
                or domain_name == settings.MESSAGES_TESTDOMAIN
            ):
                mailbox = models.Mailbox.objects.create(
                    local_part=local_part, domain=maildomain
                )
            else:
                logger.warning(
                    "Mailbox not found for delivery: %s",
                    recipient,
                )
                return

        # TODO: better thread grouping algorithm
        thread = models.Thread.objects.filter(
            subject=parsed_email["subject"], mailbox=mailbox
        ).first()

        if not thread:
            snippet = ""
            if parsed_email.get("textBody"):
                snippet = parsed_email["textBody"][0].get("content", "")[:140]
            elif parsed_email.get("htmlBody"):
                html_content = parsed_email["htmlBody"][0].get("content", "")
                clean_text = re.sub("<[^>]+>", " ", html_content)
                snippet = html.unescape(clean_text).strip()[:140]
                snippet = " ".join(snippet.split())

            thread = models.Thread.objects.create(
                subject=parsed_email["subject"], mailbox=mailbox, snippet=snippet
            )

        sender_email = parsed_email.get("from", {}).get("email")
        sender_name = parsed_email.get("from", {}).get("name")
        sender_contact, _ = models.Contact.objects.get_or_create(
            email=sender_email, defaults={"name": sender_name or sender_email}
        )

        subject = parsed_email.get("subject", "")

        message = models.Message.objects.create(
            thread=thread,
            sender=sender_contact,
            subject=subject,
            raw_mime=raw_data,
            # TODO document date fields better
            sent_at=parsed_email.get("date", timezone.now()),
            received_at=timezone.now(),
        )

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
                except (DjangoDbError, ValidationError) as e:
                    logger.error("Error creating recipient: %s", e)
                    continue
