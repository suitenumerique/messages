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

logger = logging.getLogger(__name__)


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

            # Validate email hash if there's a body
            if request.body:
                body_hash = hashlib.sha256(request.body).hexdigest()
                if body_hash != payload["body_hash"]:
                    raise jwt.InvalidTokenError("Invalid email hash")

            User = get_user_model()
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
        url_path="address_exists",
        parser_classes=[drf.parsers.BaseParser],
    )
    def address_exists(self, request):
        """Handle incoming email from MTA"""

        # Validate content type
        if request.content_type != "application/json":
            return drf.response.Response(
                {"detail": "Content-Type must be application/json"},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
        url_path="incoming_mail",
        parser_classes=[drf.parsers.BaseParser],
    )
    def incoming_mail(self, request):
        """Handle incoming email from MTA"""

        # Validate content type
        if request.content_type != "message/rfc822":
            return drf.response.Response(
                {"detail": "Content-Type must be message/rfc822"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # The JWT payload is now available in request.auth
        payload = request.auth
        if not payload:
            return drf.response.Response(
                {"detail": "Valid authorization required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        raw_data = request.body

        logger.info(
            "Raw email received: %d bytes for %s",
            len(raw_data),
            payload["original_recipients"][0:4],
        )

        # Parse the email message
        email_message = email.message_from_bytes(raw_data)

        # Print details of the email message
        logger.info("Subject: %s", email_message["Subject"])
        logger.info("From: %s", email_message["From"])
        logger.info("To: %s", email_message["To"])

        # Walk mime parts and display their metadata
        for part in email_message.walk():
            logger.info("Part: %s", part.get_content_type())
            logger.info("Content-Disposition: %s", part.get("Content-Disposition"))

        return drf.response.Response({"status": "ok"})
