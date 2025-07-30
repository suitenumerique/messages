"""
Django app configuration for deep_search.
"""

from django.apps import AppConfig


class DeepSearchConfig(AppConfig):
    """Configuration for the deep_search Django app."""
    
    default_auto_field = "django.db.models.BigAutoField"
    name = "deep_search"
    verbose_name = "Deep Search"
    
    def ready(self):
        """Called when the app is ready."""
        pass
