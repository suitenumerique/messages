"""Abstract base class for entitlements backends."""

from abc import ABC, abstractmethod


class EntitlementsBackend(ABC):
    """Abstract base class that defines the interface for entitlements backends."""

    def __init__(self, **kwargs):
        pass

    @abstractmethod
    def get_user_entitlements(self, user_sub, user_email, access_token=None):
        """Fetch user entitlements.

        Returns:
            dict: {
                "can_access": bool,
                "can_admin_maildomains": list[str],
                "operator": dict | None,
            }

        Raises:
            EntitlementsUnavailableError: If the backend cannot be reached.
        """

    @abstractmethod
    def get_mailbox_entitlements(self, mailbox_email, access_token=None):
        """Fetch mailbox entitlements.

        Returns:
            dict: {
                "max_storage": int | None,
                "storage_used": int | None,
            }

        Raises:
            EntitlementsUnavailableError: If the backend cannot be reached.
        """
