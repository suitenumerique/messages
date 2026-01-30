"""Dummy entitlements backend for development and testing."""

from core.entitlements.backends.base import EntitlementsBackend


class DummyEntitlementsBackend(EntitlementsBackend):
    """Dummy backend that always grants access with no storage limits."""

    def get_user_entitlements(self, user_sub, user_email, access_token=None):
        return {
            "can_access": True,
            "can_admin_maildomains": [],
            "operator": None,
        }

    def get_mailbox_entitlements(self, mailbox_email, access_token=None):
        return {
            "max_storage": None,
            "storage_used": None,
        }
