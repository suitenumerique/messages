"""Widget channel implementation for receiving messages from web widgets."""

import logging
import hashlib
import secrets
from typing import Dict, Any, Optional

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from django.contrib.auth.models import User

from core import models
from core.mda.rfc5322 import parse_email_message


logger = logging.getLogger(__name__)


class WidgetAuthentication(BaseAuthentication):
    """
    Custom authentication for widget endpoints using either API keys or temporary tokens.
    Returns None or (user, auth)
    """

    def authenticate(self, request):
        # Try API key authentication first
        channel_id = request.headers.get("X-Channel-ID")
        api_key = request.headers.get("X-API-Key")
        
        if channel_id and api_key:
            # API key authentication for check endpoint
            try:
                channel = models.Channel.objects.get(id=channel_id)
            except models.Channel.DoesNotExist:
                raise AuthenticationFailed("Invalid channel_id")

            expected_api_key = channel.settings.get("api_key")
            if not expected_api_key:
                raise AuthenticationFailed("Channel not configured")

            if api_key != expected_api_key:
                raise AuthenticationFailed("Invalid api_key")

            service_account = User()
            return (service_account, {"channel": channel})
        
        # Try token authentication for deliver endpoint
        token = request.headers.get("X-Widget-Token")
        if token:
            cache_key = f"widget_token:{token}"
            channel_info = cache.get(cache_key)

            if not channel_info:
                raise AuthenticationFailed("Invalid or expired token")

            try:
                channel = models.Channel.objects.get(id=channel_info["channel_id"])
            except models.Channel.DoesNotExist:
                raise AuthenticationFailed("Channel not found")

            # Verify API key matches
            if channel_info["api_key"] != channel.settings.get("api_key"):
                raise AuthenticationFailed("Token validation failed")

            # Delete the token from cache (one-time use)
            cache.delete(cache_key)

            service_account = User()
            return (service_account, {"channel": channel})
        
        # No valid authentication found
        raise AuthenticationFailed("Missing authentication headers")

    def authenticate_header(self, request):
        """Return the header to be used in the WWW-Authenticate response header."""
        return 'API-Key realm="Widget"'


class InboundWidgetViewSet(viewsets.GenericViewSet):
    """Handles incoming messages from web widgets."""
    
    # Channel metadata
    CHANNEL_TYPE = "widget"
    CHANNEL_DESCRIPTION = "Web widgets and forms"

    permission_classes = [IsAuthenticated]
    authentication_classes = [WidgetAuthentication]

    @action(
        detail=False,
        methods=["post"],
        url_path="config",
        url_name="inbound-widget-config"
    )
    def config(self, request):
        """Return the configuration for the widget."""
        try:
            data = request.data
            
            # Get channel from request.auth (set by authentication)
            auth_data = request.auth
            if not auth_data or "channel" not in auth_data:
                return Response(
                    {"detail": "Authentication failed"}, 
                    status=status.HTTP_401_UNAUTHORIZED
                )
            
            channel = auth_data["channel"]
            
            # Generate a temporary token (1 minute expiry)
            token = secrets.token_urlsafe(32)
            cache_key = f"widget_token:{token}"
            
            # Store channel info in cache for 1 minute
            cache.set(cache_key, {
                "channel_id": channel.id,
                "api_key": channel.settings.get("api_key")
            }, 60)  # 60 seconds expiry
            
            return Response({
                "status": "success",
                "token": token,
                "expires_in": 60
            })
            
        except Exception as e:
            logger.error("Error generating widget token: %s", str(e))
            return Response(
                {"detail": "Error generating token"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(
        detail=False,
        methods=["post"],
        url_path="deliver",
        url_name="inbound-widget-deliver"
    )
    def deliver(self, request):
        """Handle incoming widget message."""
        try:
            data = request.data
            
            # Get channel from request.auth (set by authentication)
            auth_data = request.auth
            if not auth_data or "channel" not in auth_data:
                return Response(
                    {"detail": "Authentication failed"}, 
                    status=status.HTTP_401_UNAUTHORIZED
                )
            
            channel = auth_data["channel"]
            
            # Extract message data
            sender_name = data.get("name", "Unknown")
            sender_email = data.get("email")
            subject = data.get("subject", "Message from widget")
            message_text = data.get("message", "")
            
            if not sender_email:
                return Response(
                    {"detail": "Missing email"}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
                
            if not message_text:
                return Response(
                    {"detail": "Missing message"}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get the target mailbox
            mailbox = channel.mailbox
            if not mailbox:
                return Response(
                    {"detail": "No mailbox configured for this channel"}, 
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            # Create or get the sender contact
            sender_contact, _ = models.Contact.objects.get_or_create(
                email=sender_email,
                mailbox=mailbox,
                defaults={"name": sender_name}
            )
            
            # Create the message content as a simple email
            email_content = self._create_email_content(
                sender_name, sender_email, subject, message_text, mailbox
            )
            
            # Parse the email content to create a proper message
            try:
                parsed_email = parse_email_message(email_content.encode('utf-8'))
            except Exception as e:
                logger.error("Failed to parse widget email content: %s", str(e))
                return Response(
                    {"detail": "Failed to process message content"}, 
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            # Create the message using the parsed email
            message = models.Message.objects.create(
                mailbox=mailbox,
                sender_contact=sender_contact,
                subject=subject,
                content=message_text,
                raw_content=email_content,
                is_draft=False,
                is_read=False
            )

            logger.info(
                "Successfully created message from widget for channel %s, sender: %s",
                channel.id, sender_email
            )
            
            return Response({
                "status": "success",
                "message_id": str(message.id)
            })
            
        except Exception as e:
            logger.error("Error delivering widget message: %s", str(e))
            return Response(
                {"detail": "Error delivering message"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _create_email_content(self, sender_name: str, sender_email: str, 
                            subject: str, message_text: str, mailbox: models.Mailbox) -> str:
        """Create email content from widget data."""
        return f"""From: {sender_name} <{sender_email}>
To: {mailbox}
Subject: {subject}
Content-Type: text/plain; charset=utf-8

{message_text}"""
    