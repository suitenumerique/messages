"""Local entitlements backend for development and testing."""

from core.entitlements.backends.base import EntitlementsBackend


class LocalEntitlementsBackend(EntitlementsBackend):
    """Local backend that always grants access. Returns None for
    can_admin_maildomains to signal that domain admin sync is not supported."""

    def get_user_entitlements(
        self, user_sub, user_email, user_info=None, force_refresh=False
    ):
        return {
            "can_access": True,
            "can_admin_maildomains": None,
        }
