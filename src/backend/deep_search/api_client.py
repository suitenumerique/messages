"""
API client for interacting with Albert API.

This module handles all low-level API communication with the Albert service.
"""

import json
import logging
import requests
from typing import Dict, List, Optional, Any

from .config import AlbertConfig
from .exceptions import AlbertAPIError

logger = logging.getLogger(__name__)


class AlbertAPIClient:
    """Client for interacting with Albert API."""
    
    def __init__(self, config: AlbertConfig):
        """
        Initialize the Albert API client.
        
        Args:
            config: Configuration for Albert API
        """
        self.config = config
        
        # Validate that base_url is properly configured
        if not self.config.base_url:
            raise ValueError("AI_BASE_URL environment variable is not set or is empty. Please configure it with the Albert API base URL.")
        
        if not self.config.base_url.startswith(('http://', 'https://')):
            raise ValueError(f"Invalid base URL '{self.config.base_url}'. URL must start with http:// or https://")
        
        logger.info(f"Albert API Client initialized with base URL: {self.config.base_url}")
        
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.config.api_key}',
            'Content-Type': 'application/json',
        })
    
    def make_request(
        self, 
        messages: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """
        Make a request to Albert API.
        
        Args:
            messages: List of message objects for the conversation
            
        Returns:
            Response from Albert API
            
        Raises:
            AlbertAPIError: If API request fails
        """
        try:
            payload = {
                "model": self.config.model,
                "messages": messages,
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
            }
           
            logger.info(f"Making request to Albert API with {len(messages)} messages")
            
            response = self.session.post(
                f"{self.config.base_url}/chat/completions",
                json=payload,
                timeout=self.config.timeout
            )
            response.raise_for_status()
            
            result = response.json()
            logger.info("Successfully received response from Albert API")
            return result
            
        except requests.RequestException as e:
            logger.error(f"Failed to make request to Albert API: {e}")
            raise AlbertAPIError(f"API request failed: {e}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON response from Albert API: {e}")
            raise AlbertAPIError(f"Invalid JSON response: {e}")
