"""MTA channel implementation for handling email delivery."""

import hashlib
import logging
import secrets

from django.conf import settings

import jwt
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.authentication import BaseAuthentication
from rest_framework.decorators import action
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core import models
from core.mda.inbound import check_local_recipient, deliver_inbound_message
from core.mda.rfc5322 import EmailParseError, parse_email_message

logger = logging.getLogger(__name__)


class MTAJWTAuthentication(BaseAuthentication):
    """
    Custom authentication for MTA endpoints using JWT tokens with email hash validation.
    Returns None or (user, auth)
    """

    def authenticate(self, request):
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return None

        try:
            jwt_token = auth_header.split(" ")[1]
            payload = jwt.decode(
                jwt_token,
                settings.MDA_API_SECRET,
                algorithms=["HS256"],
                options={
                    "require": ["exp"],
                    "verify_exp": True,
                    "verify_signature": True,
                },
            )

            if not payload.get("exp"):
                raise jwt.InvalidTokenError("Missing expiration time")

            # Validate email hash if there's a body
            if request.body:
                body_hash = hashlib.sha256(request.body).hexdigest()
                if not secrets.compare_digest(body_hash, payload["body_hash"]):
                    raise jwt.InvalidTokenError("Invalid email hash")

            service_account = models.User()
            return (service_account, payload)

        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError) as e:
            raise AuthenticationFailed("Invalid token") from e
        except (IndexError, KeyError) as e:
            raise AuthenticationFailed("Invalid token header or payload") from e

    def authenticate_header(self, request):
        """Return the header to be used in the WWW-Authenticate response header."""
        return 'Bearer realm="MTA"'


class InboundMTAViewSet(viewsets.GenericViewSet):
    """Handles incoming email messages from MTA (Mail Transfer Agent)."""

    # Channel metadata
    CHANNEL_TYPE = "mta"
    CHANNEL_DESCRIPTION = "Mail Transfer Agent (email)"

    permission_classes = [IsAuthenticated]
    authentication_classes = [MTAJWTAuthentication]

    @extend_schema(exclude=True)
    @action(
        detail=False, methods=["post"], url_path="check", url_name="inbound-mta-check"
    )
    def check(self, request):
        """Check recipients exist."""
        data = request.data
        addresses = data.get("addresses", [])
        if not addresses or not isinstance(addresses, list):
            return Response(
                {"detail": "Missing addresses"}, status=status.HTTP_400_BAD_REQUEST
            )

        results = {
            address: check_local_recipient(address, create_if_missing=False)
            for address in addresses
        }
        return Response(results)

    @extend_schema(exclude=True)
    @action(
        detail=False,
        methods=["post"],
        url_path="deliver",
        url_name="inbound-mta-deliver",
    )
    def deliver(self, request):
        """Handle incoming raw email (message/rfc822) from MTA."""

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

        def sanitize_header(header: str) -> str:
            return header.replace("\r", "").replace("\n", "")[0:255]

        if "client_helo" in mta_metadata:
            prepend_headers = [
                (
                    "Received",
                    f"from {mta_metadata['client_helo']} ("
                    + f"{mta_metadata['client_hostname']} [{mta_metadata['client_address']}]);",
                ),
            ]

            raw_data = (
                "\r\n".join([f"{k}: {sanitize_header(v)}" for k, v in prepend_headers])
                + "\r\n"
            ).encode("utf-8") + raw_data

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
