"""Abstract base class for entitlements backends."""

from abc import ABC, abstractmethod


class EntitlementsBackend(ABC):
    """Abstract base class that defines the interface for entitlements backends."""

    @abstractmethod
    def get_user_entitlements(
        self, user_sub, user_email, user_info=None, force_refresh=False
    ):
        """Fetch user entitlements.

        Args:
            user_sub: The user's OIDC subject identifier.
            user_email: The user's email address.
            user_info: The full OIDC user_info dict (backends may extract claims from it).
            force_refresh: If True, bypass any cache and fetch fresh data.

        Returns:
            dict: {
                "can_access": bool,
                "can_admin_maildomains": list[str] | None,
            }

        Raises:
            EntitlementsUnavailableError: If the backend cannot be reached.
        """

    def get_mailbox_entitlements(  # pylint: disable=unused-argument
        self, mailbox, force_refresh=False
    ):
        """Fetch storage entitlements for a mailbox.

        Quotas are attached to mailboxes, not users: a user object never
        carries a quota, so callers always resolve entitlements through the
        mailbox they are viewing.

        The result carries two levels — the mailbox ("account") and, when the
        mailbox's domain is tied to an organization, the aggregate for that
        organization. A ``max_storage`` of ``None`` means "no limit known"
        and the frontend hides the corresponding gauge.

        Returns:
            dict: {
                "account": {"storage_used": int, "max_storage": int | None},
                "organization": {
                    "storage_used": int,
                    "max_storage": int | None,
                } | None,
            }

        Raises:
            EntitlementsUnavailableError: If the backend cannot be reached.
        """
        return {
            "account": {"storage_used": 0, "max_storage": None},
            "organization": None,
        }
