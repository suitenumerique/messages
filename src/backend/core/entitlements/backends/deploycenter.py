"""DeployCenter (Espace Operateur) entitlements backend."""

import logging

import requests

from core.entitlements.backends.base import EntitlementsBackend

logger = logging.getLogger(__name__)


class DeployCenterEntitlementsBackend(EntitlementsBackend):
    """Backend that fetches entitlements from the DeployCenter API."""

    def __init__(self, base_url, service_id, api_key, timeout=10, **kwargs):
        super().__init__(**kwargs)
        self.base_url = base_url.rstrip("/")
        self.service_id = service_id
        self.api_key = api_key
        self.timeout = timeout

    def _make_request(self, account_type, account_id, access_token=None):
        """Make a request to the DeployCenter entitlements API.

        Returns:
            dict | None: The response data, or None on failure.
        """
        url = f"{self.base_url}/api/v1.0/entitlements"
        params = {
            "service_id": self.service_id,
            "account_type": account_type,
            "account_id": account_id,
        }
        headers = {
            "X-Service-Auth": f"ApiKey {self.api_key}",
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        try:
            response = requests.get(
                url, params=params, headers=headers, timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            logger.warning(
                "DeployCenter entitlements request failed for %s/%s",
                account_type,
                account_id,
                exc_info=True,
            )
            return None

    def get_user_entitlements(self, user_sub, user_email, access_token=None):
        """Fetch user entitlements from DeployCenter.

        Raises:
            EntitlementsUnavailableError: If the request fails (fail closed).
        """
        from core.entitlements import EntitlementsUnavailableError

        data = self._make_request("user", user_email, access_token=access_token)
        if data is None:
            raise EntitlementsUnavailableError(
                "Failed to fetch user entitlements from DeployCenter"
            )
        return {
            "can_access": data.get("can_access", False),
            "can_admin_maildomains": data.get("can_admin_maildomains", []),
            "operator": data.get("operator"),
        }

    def get_mailbox_entitlements(self, mailbox_email, access_token=None):
        """Fetch mailbox entitlements from DeployCenter.

        Raises:
            EntitlementsUnavailableError: If the request fails (fail closed).
        """
        from core.entitlements import EntitlementsUnavailableError

        data = self._make_request("mailbox", mailbox_email, access_token=access_token)
        if data is None:
            raise EntitlementsUnavailableError(
                "Failed to fetch mailbox entitlements from DeployCenter"
            )
        return {
            "max_storage": data.get("max_storage"),
            "storage_used": data.get("storage_used"),
        }
