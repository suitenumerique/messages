"""Entitlements service layer with Django cache."""

import logging

from django.core.cache import cache
from django.conf import settings

from core.entitlements.factory import get_entitlements_backend

logger = logging.getLogger(__name__)


class EntitlementsUnavailableError(Exception):
    """Raised when the entitlements backend cannot be reached or returns an error."""


def get_user_entitlements(
    user_sub, user_email, access_token=None, force_refresh=False
):
    """Get user entitlements, using Django cache.

    Returns:
        dict: {"can_access": bool, "can_admin_maildomains": [str], "operator": dict|None}

    Raises:
        EntitlementsUnavailableError: If the backend cannot be reached and no cache exists.
    """
    cache_key = f"entitlements:user:{user_sub}"

    if not force_refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    backend = get_entitlements_backend()
    result = backend.get_user_entitlements(
        user_sub, user_email, access_token=access_token
    )

    cache.set(cache_key, result, settings.ENTITLEMENTS_CACHE_TIMEOUT)
    return result


def get_mailbox_entitlements(mailbox_email, access_token=None, force_refresh=False):
    """Get mailbox entitlements, using Django cache.

    Returns:
        dict: {"max_storage": int|None, "storage_used": int|None}

    Raises:
        EntitlementsUnavailableError: If the backend cannot be reached and no cache exists.
    """
    cache_key = f"entitlements:mailbox:{mailbox_email}"

    if not force_refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    backend = get_entitlements_backend()
    result = backend.get_mailbox_entitlements(
        mailbox_email, access_token=access_token
    )

    cache.set(cache_key, result, settings.ENTITLEMENTS_CACHE_TIMEOUT)
    return result
