"""Factory for creating the sender trust backend instance."""

import functools

from django.conf import settings
from django.utils.module_loading import import_string

from core.mda.sender_trust.backends.base import SenderTrustBackend


@functools.cache
def get_sender_trust_backend() -> SenderTrustBackend:
    """Return a singleton instance of the configured sender trust backend."""
    backend_class = import_string(settings.SENDER_TRUST_BACKEND)
    return backend_class(**settings.SENDER_TRUST_BACKEND_PARAMETERS)
