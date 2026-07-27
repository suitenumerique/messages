"""DeployCenter (Espace Operateur) entitlements backend."""

import logging

from django.conf import settings
from django.core.cache import cache

import requests

from core.entitlements import EntitlementsUnavailableError
from core.entitlements.backends.base import EntitlementsBackend
from core.services.storage import (
    get_mailbox_storage_used,
    get_organization_storage_used,
)

logger = logging.getLogger(__name__)


class DeployCenterEntitlementsBackend(EntitlementsBackend):
    """Backend that fetches entitlements from the DeployCenter API.

    Args:
        base_url: Full URL of the entitlements endpoint
            (e.g. "https://dc.example.com/api/v1.0/entitlements/").
        service_id: The service identifier in DeployCenter.
        api_key: API key for X-Service-Auth header.
        timeout: HTTP request timeout in seconds.
        oidc_claims: List of OIDC claim names to extract from user_info
            and forward as query params (e.g. ["siret"]).
    """

    def __init__(
        self,
        base_url,
        service_id,
        api_key,
        timeout=10,
        oidc_claims=None,
        organization_claim="siret",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.base_url = base_url
        self.service_id = service_id
        self.api_key = api_key
        self.timeout = timeout
        self.oidc_claims = oidc_claims or []
        self.organization_claim = organization_claim

    def _cache_key(self, user_sub):
        return f"entitlements:user:{user_sub}"

    def _mailbox_cache_key(self, mailbox_id):
        return f"entitlements:mailbox:{mailbox_id}"

    def _make_request(self, user_email, user_info=None):
        """Make a request to the DeployCenter entitlements API.

        Returns:
            dict | None: The response data, or None on failure.
        """
        params = {
            "service_id": self.service_id,
            "account_type": "user",
            "account_email": user_email,
        }

        # Forward configured OIDC claims as query params
        if user_info:
            for claim in self.oidc_claims:
                if claim in user_info:
                    params[claim] = user_info[claim]

        headers = {
            "X-Service-Auth": f"Bearer {self.api_key}",
        }

        try:
            response = requests.get(
                self.base_url, params=params, headers=headers, timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            email_domain = user_email.split("@")[-1] if "@" in user_email else "?"
            logger.warning(
                "DeployCenter entitlements request failed for user@%s",
                email_domain,
                exc_info=True,
            )
            return None

    def get_user_entitlements(
        self, user_sub, user_email, user_info=None, force_refresh=False
    ):
        """Fetch user entitlements from DeployCenter with caching.

        On cache miss or force_refresh: fetches from the API.
        On API failure: falls back to stale cache if available,
        otherwise raises EntitlementsUnavailableError.
        """
        cache_key = self._cache_key(user_sub)

        if not force_refresh:
            cached = cache.get(cache_key)
            if cached is not None:
                return cached

        data = self._make_request(user_email, user_info=user_info)

        if data is None:
            # API failed — try stale cache as fallback
            cached = cache.get(cache_key)
            if cached is not None:
                return cached
            raise EntitlementsUnavailableError(
                "Failed to fetch user entitlements from DeployCenter"
            )

        entitlements = data.get("entitlements", {})
        result = {
            "can_access": entitlements.get("can_access", False),
            "can_admin_maildomains": entitlements.get("can_admin_maildomains"),
        }

        cache.set(cache_key, result, settings.ENTITLEMENTS_CACHE_TIMEOUT)
        return result

    def _build_usage_metrics(self, mailbox, org_value):
        """Build the usage-metric entries pushed to DeployCenter.

        Metrics are computed locally (same computation the global metrics
        endpoint exposes) and pushed in the POST body so DeployCenter does not
        have to scrape us back. The organization entry is only included when
        the mailbox's domain is tied to an organization.

        ``storage_used`` here is the per-message "storage felt" basis (a
        sha-shared blob counts once per referencing message), so DeployCenter's
        quota decision uses the same basis as the in-app gauge. This is
        intentional and settled — see ``core.services.storage``.

        Read through the cached accessors (``get_*``, TTL
        ``STORAGE_USAGE_CACHE_TTL``) rather than recomputing. This runs on every
        entitlements cache miss, inside a user-facing request, and the
        organization figure aggregates the five-subquery annotation across every
        mailbox in the organization — far too expensive to put in front of a
        sidebar load. The cost is that DeployCenter may see a figure up to one
        TTL stale, which is well inside the entitlements cache window it is
        already being read back through. The ``/metrics`` scrape endpoints still
        compute live.
        """
        mailbox_email = f"{mailbox.local_part}@{mailbox.domain.name}"
        mailbox_entry = {
            "account": {"type": "mailbox", "email": mailbox_email},
            "metrics": {"storage_used": get_mailbox_storage_used(mailbox)},
        }
        if org_value is None:
            return [mailbox_entry]

        mailbox_entry[self.organization_claim] = org_value
        organization_entry = {
            "account": {"type": "organization"},
            "metrics": {
                "storage_used": get_organization_storage_used(
                    self.organization_claim, org_value
                )
            },
            self.organization_claim: org_value,
        }
        return [mailbox_entry, organization_entry]

    def _fetch_mailbox_entitlements(self, mailbox, org_value):
        """POST usage metrics and read back storage limits for a mailbox.

        Returns the parsed DeployCenter response, or None on failure.
        """
        mailbox_email = f"{mailbox.local_part}@{mailbox.domain.name}"
        params = {
            "account_type": "mailbox",
            "account_email": mailbox_email,
            "service_id": self.service_id,
        }
        if org_value is not None:
            params[self.organization_claim] = org_value

        headers = {"X-Service-Auth": f"Bearer {self.api_key}"}

        try:
            response = requests.post(
                self.base_url,
                params=params,
                json={"usage_metrics": self._build_usage_metrics(mailbox, org_value)},
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            logger.warning(
                "DeployCenter mailbox entitlements request failed for %s",
                mailbox.domain.name,
                exc_info=True,
            )
            return None

    def get_mailbox_entitlements(self, mailbox, force_refresh=False):
        """Fetch storage entitlements for a mailbox from DeployCenter, cached.

        On API failure, falls back to a stale cache if available, otherwise
        raises EntitlementsUnavailableError.
        """
        cache_key = self._mailbox_cache_key(mailbox.id)

        if not force_refresh:
            cached = cache.get(cache_key)
            if cached is not None:
                return cached

        org_value = (mailbox.domain.custom_attributes or {}).get(
            self.organization_claim
        )
        data = self._fetch_mailbox_entitlements(mailbox, org_value)

        if data is None:
            cached = cache.get(cache_key)
            if cached is not None:
                return cached
            raise EntitlementsUnavailableError(
                "Failed to fetch mailbox entitlements from DeployCenter"
            )

        entitlements = data.get("entitlements", {})
        metrics = data.get("metrics", {})

        account = {
            "storage_used": (metrics.get("account") or {}).get("storage_used", 0),
            "max_storage": entitlements.get("max_storage_account"),
        }

        organization = None
        if org_value is not None:
            organization = {
                "storage_used": (metrics.get("organization") or {}).get(
                    "storage_used", 0
                ),
                "max_storage": entitlements.get("max_storage_organization"),
            }

        result = {"account": account, "organization": organization}
        cache.set(cache_key, result, settings.ENTITLEMENTS_CACHE_TIMEOUT)
        return result
