"""Generic inbound API for handling different channel types."""

import logging
from typing import Dict, Any

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.core.exceptions import ValidationError


from rest_framework import status, viewsets
from rest_framework.authentication import BaseAuthentication
from rest_framework.decorators import action
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.parsers import JSONParser, BaseParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core import models
from core.channels import load_channel

logger = logging.getLogger(__name__)


class InboundViewSet(viewsets.GenericViewSet):
    """Generic ViewSet for handling inbound messages from various channels."""
    
    permission_classes = [IsAuthenticated]  # All channels require authentication
    parser_classes = [JSONParser, BaseParser]
    
    def get_authentication_classes(self):
        """Return authentication classes based on channel type."""
        # Get channel type from URL parameters
        channel_type = self.kwargs.get('channel_type')
        
        # Get the processor class for this channel type
        processor_class = load_channel(channel_type)
        if not processor_class:
            raise ValueError(f"Unknown channel type")
        
        return processor_class.get_authentication_classes()

    @action(
        detail=False,
        methods=["post"],
        url_path="check",
        url_name="inbound-check"
    )
    def check(self, request, channel_type=None):
        """
        Generic check endpoint.
        """
        try:
            # Get the appropriate processor for this channel type
            processor_class = load_channel(channel_type)
            processor = processor_class()
            
            # Let the processor handle the check logic
            return processor.check(request)

        except Exception as e:
            logger.error("Error in check operation: %s", str(e))
            return Response(
                {
                    "status": "error",
                    "detail": "Error in check operation"
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    

    @action(
        detail=False,
        methods=["post"],
        url_path="deliver",
        url_name="inbound-deliver"
    )
    def deliver(self, request, channel_type=None):
        """
        Generic deliver endpoint.
        """
        try:
            # Get the appropriate processor for this channel type
            processor_class = load_channel(channel_type)
            processor = processor_class()
            
            # Let the processor handle the delivery logic
            return processor.deliver(request)
            
        except Exception as e:
            logger.error("Error in deliver operation: %s", str(e))
            return Response(
                {
                    "status": "error",
                    "detail": "Error in deliver operation"
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            ) 