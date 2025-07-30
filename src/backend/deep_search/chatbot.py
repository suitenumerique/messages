"""
Simplified chatbot implementation using Albert API for intelligent email search only.

This module provides a minimal interface for email search operations through
the Albert API. All logic has been moved to views.py for simplicity.

Author: ANCT
Date: 2025-06-19
Refactored: 2025-06-26
Updated: 2025-07-02
Simplified: 2025-07-08
"""

import logging
from typing import Optional

from .config import AlbertConfig
from .api_client import AlbertAPIClient
from .exceptions import ChatbotError

logger = logging.getLogger(__name__)


class AlbertChatbot:
    """
    This class only provides API client configuration.
    All search logic is in views.py.
    """

    def __init__(self, config: Optional[AlbertConfig] = None):
        """
        Initialize the Albert chatbot with API configuration.
        
        Args:
            config: Configuration for Albert API.
        """
        self.config = AlbertConfig()
        self.api_client = AlbertAPIClient(self.config)
        logger.info("Albert chatbot initialized for intelligent email search")


def get_chatbot(config: Optional[AlbertConfig] = None) -> AlbertChatbot:
    """
    Factory function to get a configured AlbertChatbot instance.
    
    Args:
        config: Optional configuration for Albert API. If None, uses default config.
        
    Returns:
        Configured AlbertChatbot instance
    """
    return AlbertChatbot(config=config)
