"""
Configuration settings for the Albert chatbot.

This module contains configuration classes and settings for the chatbot system.
"""

import os
from dataclasses import dataclass
from enum import Enum

@dataclass
class AlbertConfig:
    """Configuration for Albert API."""
    name: str = "albert-etalab"
    base_url: str = os.getenv("AI_BASE_URL", "https://albert.api.etalab.gouv.fr/v1")
    api_key: str = os.getenv("AI_API_KEY", "")
    model: str = os.getenv("AI_MODEL", "albert-large")
    temperature: float = 0.3
    max_tokens: int = 4000  # Increased for better response capacity
    timeout: int = 120
    max_iterations: int = 5
    # RAG-specific settings
    max_emails_per_batch: int = 50  # Legacy limit (not currently used - all emails are processed)
    max_email_content_length: int = 15000  # Limit email content size (increased to preserve attachments)
    batch_upload_delay: float = 2.0  # Delay between batch uploads
    min_relevance_score_percentage: float = 0.95  # Minimum score as percentage of highest score (0.0-1.0)
