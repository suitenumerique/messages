"""Local entitlements backend, the default when no DeployCenter is connected."""

from core.entitlements.backends.base import EntitlementsBackend
from core.services.storage import (
    get_mailbox_storage_used,
    get_organization_storage_used,
)


class LocalEntitlementsBackend(EntitlementsBackend):
    """Local backend that always grants access and computes storage from the DB.

    ``can_admin_maildomains`` is None to signal that domain admin sync is not
    supported. Storage usage is computed from local data; the limits are
    static config (``ENTITLEMENTS_BACKEND_PARAMETERS``). Both default to no
    limit, which hides the quota gauges: an instance never shows a quota it
    was not given.
    """

    def __init__(
        self,
        mailbox_storage_limit=None,
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
        """Return the configured limits with the usage computed from the DB.

        Nothing is cached here beyond the storage service's own cache, so
        ``force_refresh`` has nothing to bypass.
        """
        account = {
            "storage_used": get_mailbox_storage_used(mailbox),
            "max_storage": self.mailbox_storage_limit,
        }

        organization = None
        # An empty claim means no organization, as in the DeployCenter backend.
        org_value = (mailbox.domain.custom_attributes or {}).get(
            self.organization_claim
        ) or None
        if org_value is not None:
            organization = {
                "storage_used": get_organization_storage_used(
                    self.organization_claim, org_value
                ),
                "max_storage": self.organization_storage_limit,
            }

        return {"account": account, "organization": organization}
