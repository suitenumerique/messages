"""Local entitlements backend for development and testing."""

from core.entitlements.backends.base import EntitlementsBackend
from core.services.storage import (
    get_mailbox_storage_used,
    get_organization_storage_used,
)

# Default per-mailbox storage limit (bytes) surfaced by the gauge in local
# development. Kept as a visible, round value so the widget is exercised
# out of the box; production deployments use the DeployCenter backend.
DEFAULT_MAILBOX_STORAGE_LIMIT = 5 * 1000**3  # 5 GB


class LocalEntitlementsBackend(EntitlementsBackend):
    """Local backend that always grants access and computes storage from the DB.

    ``can_admin_maildomains`` is None to signal that domain admin sync is not
    supported. Storage usage is computed from local data; the limits are
    static config so the quota gauge can be exercised without DeployCenter.
    """

    def __init__(
        self,
        mailbox_storage_limit=DEFAULT_MAILBOX_STORAGE_LIMIT,
        organization_storage_limit=None,
        organization_claim="siret",
    ):
        # ``None`` means "no limit" — the frontend then hides that gauge.
        self.mailbox_storage_limit = (
            int(mailbox_storage_limit) if mailbox_storage_limit is not None else None
        )
        self.organization_storage_limit = (
            int(organization_storage_limit)
            if organization_storage_limit is not None
            else None
        )
        self.organization_claim = organization_claim

    def get_user_entitlements(
        self, user_sub, user_email, user_info=None, force_refresh=False
    ):
        return {
            "can_access": True,
            "can_admin_maildomains": None,
        }

    def get_mailbox_entitlements(self, mailbox, force_refresh=False):
        account = {
            "storage_used": get_mailbox_storage_used(mailbox),
            "max_storage": self.mailbox_storage_limit,
        }

        organization = None
        org_value = (mailbox.domain.custom_attributes or {}).get(
            self.organization_claim
        )
        if org_value:
            organization = {
                "storage_used": get_organization_storage_used(
                    self.organization_claim, org_value
                ),
                "max_storage": self.organization_storage_limit,
            }

        return {"account": account, "organization": organization}
