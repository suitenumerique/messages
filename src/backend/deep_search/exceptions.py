"""
Custom exceptions for the chatbot module.
"""


class ChatbotError(Exception):
    """Base exception for chatbot-related errors."""
    pass

class AlbertAPIError(ChatbotError):
    """Exception raised when Albert API requests fail."""
    pass


class ConfigurationError(ChatbotError):
    """Exception raised when there are configuration issues."""
    pass


class EmailRetrievalError(ChatbotError):
    """Exception raised when email retrieval operations fail."""
    pass
