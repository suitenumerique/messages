"""DRF Views for MTA endpoints"""

import hashlib
import logging

from django.conf import settings
from django.contrib.auth import get_user_model

import jwt
import rest_framework as drf
from rest_framework import authentication, parsers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core import models
from core.mda.delivery import deliver_inbound_message
from core.mda.rfc5322 import EmailParseError, parse_email_message

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
        except (IndexError, KeyError) as e:
            # Handle cases where header is malformed or payload is missing keys
            raise drf.exceptions.AuthenticationFailed(
                "Invalid token header or payload"
            ) from e

    def authenticate_header(self, request):
        """Return the header to be used in the WWW-Authenticate response header."""
        return 'Bearer realm="MTA"'


class MTAViewSet(viewsets.GenericViewSet):
    """ViewSet for MTA-related endpoints"""

    permission_classes = [IsAuthenticated]
    authentication_classes = [MTAJWTAuthentication]

    @action(
        detail=False,
        methods=["post"],
        url_path="check-recipients",
        parser_classes=[parsers.JSONParser],
    )
    def check_recipients(self, request):
        """Check if recipient email addresses exist for the MTA."""
        # Get a list of email addresses from the request body
        email_addresses = request.data.get("addresses")
        if not email_addresses or not isinstance(email_addresses, list):
            return Response(
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
            try:
                local_part, domain_name = email_address.split("@", 1)
            except ValueError:
                ret[email_address] = False  # Invalid format
                continue

            if settings.MESSAGES_TESTDOMAIN == domain_name:
                ret[email_address] = True
                continue

            # Check if the email address exists in the database
            ret[email_address] = models.Mailbox.objects.filter(
                local_part=local_part,
                domain__name=domain_name,
                # is_active=True, # Removed: Mailbox model has no is_active field
            ).exists()

        return Response(ret)

    @action(
        detail=False,
        methods=["post"],
        url_path="inbound-email",
        # Use ByteParser to handle raw message/rfc822 directly
        parser_classes=[parsers.BaseParser],  # Keep BaseParser if JWT needs body hash
    )
    def inbound_email(self, request):
        """Handle incoming raw email (message/rfc822) from MTA."""

        # Authentication is handled by MTAJWTAuthentication
        # request.user will be the service account, request.auth the JWT payload
        mta_metadata = request.auth
        if not mta_metadata or "original_recipients" not in mta_metadata:
            # This case should ideally be caught by the authentication class
            logger.error("MTA metadata missing or malformed in authenticated request.")
            return Response(
                {"detail": "Internal authentication error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Validate content type (optional but good practice)
        # Note: If parser_classes included FormParser or MultiPartParser, request.body might be consumed.
        # Ensure parser_classes=[parsers.BaseParser] or similar if relying on request.body.
        if request.content_type != "message/rfc822":
            logger.warning(
                "Received inbound POST with incorrect Content-Type: %s",
                request.content_type,
            )
            # Decide whether to reject or attempt parsing anyway
            return Response(
                {"detail": "Content-Type must be message/rfc822"},
                status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )

        raw_data = request.body
        if not raw_data:
            logger.error("Received empty body for inbound email.")
            return Response(
                {"status": "error", "detail": "Empty request body"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info(
            "Raw email received: %d bytes for %s",
            len(raw_data),
            mta_metadata["original_recipients"],  # Log all intended recipients
        )

        # Parse the email message once
        try:
            parsed_email = parse_email_message(raw_data)
        except EmailParseError as e:
            logger.error("Failed to parse inbound email: %s", str(e))
            # Consider saving the raw email for debugging
            return Response(
                {"status": "error", "detail": "Failed to parse email"},
                status=status.HTTP_400_BAD_REQUEST,  # Bad request as email is malformed
            )

        # Deliver the parsed email to each original recipient
        success_count = 0
        failure_count = 0
        delivery_results = {}

        for recipient in mta_metadata["original_recipients"]:
            try:
                # Call the refactored delivery function which returns True/False
                delivered = deliver_inbound_message(recipient, parsed_email, raw_data)
                if delivered:
                    success_count += 1
                    delivery_results[recipient] = "Success"
                else:
                    # Delivery function failed (and logged the reason)
                    failure_count += 1
                    delivery_results[recipient] = "Failed"
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error(
                    "Unexpected error during delivery loop for %s: %s",
                    recipient,
                    e,
                    exc_info=True,
                )
                failure_count += 1
                delivery_results[recipient] = f"Error: {e}"

        # Determine overall status based on counts
        if failure_count > 0 and success_count == 0:
            # If all deliveries failed, return a server error
            logger.error("All deliveries failed for inbound email.")
            return Response(
                {
                    "status": "error",
                    "detail": "Failed to deliver message to any recipient",
                    "results": delivery_results,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if failure_count > 0:
            # If some deliveries failed, return 207 Multi-Status
            logger.warning(
                "Partial delivery failure: %d successful, %d failed",
                success_count,
                failure_count,
            )
            return Response(
                {
                    "status": "partial_success",
                    "delivered": success_count,
                    "failed": failure_count,
                    "results": delivery_results,
                },
                status=status.HTTP_207_MULTI_STATUS,
            )

        # All deliveries successful
        logger.info("All %d deliveries successful for inbound email.", success_count)
        return Response({"status": "ok", "delivered": success_count})
