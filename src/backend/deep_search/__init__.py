"""
Chatbot module for mail processing operations using Albert API.
"""

default_app_config = 'deep_search.apps.DeepSearchConfig'

from .chatbot import AlbertChatbot, get_chatbot
from .config import AlbertConfig

__all__ = [
    "AlbertChatbot",
    "AlbertConfig", 
    "get_chatbot",
]
