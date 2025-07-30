"""
Content parsing utilities for the chatbot.
"""

import re
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class ContentParser:
    """Handles parsing and processing of email content."""
    
    def __init__(self):
        """Initialize the content parser."""
        self.email_regex = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
        self.phone_regex = re.compile(r'(?:\+33|0)[1-9](?:[0-9]{8})')
        
    def extract_emails(self, text: str) -> List[str]:
        """Extract email addresses from text."""
        if not text:
            return []
        return self.email_regex.findall(text)
    
    def extract_phone_numbers(self, text: str) -> List[str]:
        """Extract phone numbers from text."""
        if not text:
            return []
        return self.phone_regex.findall(text)
    
    def clean_content(self, content: str) -> str:
        """Clean and normalize email content."""
        if not content:
            return ""
        
        # Remove excessive whitespace
        content = re.sub(r'\s+', ' ', content)
        
        # Remove common email signatures patterns
        content = re.sub(r'--\s*\n.*', '', content, flags=re.DOTALL)
        
        return content.strip()
    
    def extract_keywords(self, content: str, max_keywords: int = 10) -> List[str]:
        """Extract important keywords from content."""
        if not content:
            return []
        
        # Simple keyword extraction - can be enhanced with NLP
        words = re.findall(r'\b[a-zA-ZÀ-ÿ]{3,}\b', content.lower())
        
        # Remove common French stop words
        stop_words = {
            'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'had', 'her', 'was', 'one', 'our', 'out', 'day', 'get', 'has', 'him', 'his', 'how', 'its', 'may', 'new', 'now', 'old', 'see', 'two', 'who', 'boy', 'did', 'she', 'use', 'way', 'win', 'yet',
            'les', 'des', 'une', 'pour', 'que', 'qui', 'dans', 'avec', 'est', 'sur', 'par', 'tout', 'mais', 'comme', 'aux', 'son', 'ses', 'ces', 'mes', 'nos', 'vos', 'leur', 'leurs', 'dont', 'où', 'quand', 'comment', 'pourquoi'
        }
        
        keywords = [word for word in words if word not in stop_words and len(word) > 3]
        
        # Count frequency and return most common
        word_count = {}
        for word in keywords:
            word_count[word] = word_count.get(word, 0) + 1
        
        sorted_words = sorted(word_count.items(), key=lambda x: x[1], reverse=True)
        return [word for word, count in sorted_words[:max_keywords]]
