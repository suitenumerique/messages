"""
Scaleway DNS provider implementation.
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

from django.conf import settings

import requests

logger = logging.getLogger(__name__)


class ScalewayDNSProvider:
    """DNS provider for Scaleway Domains and DNS service."""

    def __init__(self):
        """
        Initialize the Scaleway DNS provider.
        """
        self.api_token = settings.DNS_SCALEWAY_API_TOKEN
        self.project_id = settings.DNS_SCALEWAY_PROJECT_ID
        self.ttl = settings.DNS_SCALEWAY_TTL

        self.base_url = "https://api.scaleway.com/domain/v2beta1"
        self.headers = {
            "X-Auth-Token": self.api_token,
            "Content-Type": "application/json",
        }

    def is_configured(self) -> bool:
        """
        Check if the Scaleway DNS provider is configured.
        """
        return bool(self.api_token) and bool(self.project_id)

    def provision_domain_records(
        self, domain: str, expected_records: List[Dict[str, Any]], pretend: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Provision DNS records for a domain.
        Creates records that don't already exist, tries to update existing records if they are different.

        Args:
            domain: Domain name
            expected_records: List of expected DNS records
            pretend: If True, simulate operations without making actual changes

        Returns:
            List of changes made to the DNS records
        """

        # Create zone if it doesn't exist
        created = self.create_zone(domain, pretend)

        if created and pretend:
            all_records = []
        else:
            all_records = self.get_records(domain, paginate=True)

        records_by_type = defaultdict(list)
        for record in all_records:
            records_by_type[(record["type"].upper(), record["name"])].append(record)

        expected_records_by_type = defaultdict(list)
        for expected_record in expected_records:
            expected_records_by_type[
                (
                    expected_record["type"].upper(),
                    expected_record["target"].split(".")[-1],
                )
            ].append(expected_record)

        results = []

        for record_type, expected_records_of_type in expected_records_by_type.items():
            # We completely swap these records if they are different.
            if record_type in {("MX", ""), ("TXT", "_dmarc"), ("TXT", "_domainkey")}:
                results.extend(
                    self._sync_records(
                        expected_records_of_type,
                        records_by_type[record_type],
                        domain,
                        pretend,
                    )
                )
            else:
                for expected_record in expected_records_of_type:
                    if record_type == ("TXT", "") and expected_record[
                        "value"
                    ].startswith("v=spf1 "):
                        # This is the SPF record, we need to update it if there is an existing one.
                        existing_spf_records = [
                            record
                            for record in records_by_type[record_type]
                            if record["value"].startswith("v=spf1 ")
                        ]
                        results.extend(
                            self._sync_records(
                                [expected_record], existing_spf_records, domain, pretend
                            )
                        )
                    else:
                        raise ValueError(f"Unknown record type: {expected_record}")

        return results

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        paginate: bool = False,
    ) -> Dict[str, Any]:
        """
        Make a request to the Scaleway API.

        Args:
            method: HTTP method
            endpoint: API endpoint
            data: Request data
            paginate: If True, handle pagination for GET requests

        Returns:
            API response as dictionary

        Raises:
            Exception: If the request fails
        """
        url = f"{self.base_url}/{endpoint}"

        # Handle pagination for GET requests
        if method.upper() == "GET" and paginate:
            all_results = []
            page = 1
            page_size = 100

            # Determine the data key based on the endpoint
            if endpoint.endswith("dns-zones"):
                data_key = "dns_zones"
            elif endpoint.endswith("/records"):
                data_key = "records"
            else:
                raise ValueError(f"Unknown paginated endpoint: {endpoint}")

            while True:
                # Add pagination parameters to URL
                paginated_url = f"{url}?page={page}&page_size={page_size}"

                response = requests.request(
                    method=method, url=paginated_url, headers=self.headers, timeout=30
                )

                if not response.ok:
                    self._handle_api_error(response)

                response_data = response.json()

                # Add results from this page
                page_results = response_data.get(data_key, [])
                all_results.extend(page_results)

                # Check if we've reached the end
                if len(page_results) < page_size:
                    break

                page += 1

            # Return the combined results in the same format as the original response
            return {data_key: all_results, "total_count": len(all_results)}

        # Non-paginated request
        response = requests.request(
            method=method, url=url, headers=self.headers, json=data, timeout=30
        )

        if not response.ok:
            self._handle_api_error(response)

        return response.json()

    def _handle_api_error(self, response: requests.Response) -> None:
        """
        Handle Scaleway API errors with proper error messages.

        Args:
            response: HTTP response object

        Raises:
            Exception: With detailed error message and context
        """
        try:
            error_data = response.json()
            error_message = error_data.get("message", "Unknown error")
        except (ValueError, KeyError):
            error_message = "Unknown error"

        if response.status_code == 404:
            raise ValueError(f"Zone not found: {error_message}")
        if response.status_code == 409:
            raise ValueError(f"Zone already exists: {error_message}")
        if response.status_code == 400:
            raise ValueError(f"Invalid request: {error_message}")
        if response.status_code == 401:
            raise ValueError(f"Authentication failed: {error_message}")

        # For any other status code
        raise ValueError(f"API error ({response.status_code}): {error_message}")

    def get_records(self, domain: str, paginate: bool = False) -> List[Dict[str, Any]]:
        """
        Get all DNS records for a zone.

        Args:
            domain: Domain name

        Returns:
            List of record dictionaries
        """
        response = self._make_request(
            "GET", f"dns-zones/{domain}/records", paginate=paginate
        )
        return response.get("records", [])

    def zone_exists(self, zone_name: str) -> bool:
        """
        Checks if a zone exists for the given domain.

        Args:
            zone_name: Zone name to check

        Returns:
            True if zone exists, False otherwise
        """
        try:
            records = self.get_records(zone_name)
            return len(records) > 0
        except ValueError:
            return False

    def create_zone(self, domain: str, pretend: bool = False) -> bool:
        """
        Create a new DNS zone if it doesn't exist already.

        Args:
            domain: Domain name
            pretend: If True, simulate operations without making actual changes

        Returns:
            True if zone was created, False otherwise
        """
        if self.zone_exists(domain):
            return False

        # Does the parent zone exist?
        subdomain, parent_domain = domain.split(".", 1)
        if self.zone_exists(parent_domain):
            # Create a sub-zone
            self._create_zone_with_parent(subdomain, parent_domain, pretend)
        else:
            # Create a new root zone
            self._create_zone_with_parent("", domain, pretend)

        return True

    def _create_zone_with_parent(
        self, subdomain: str, parent_domain: str, pretend: bool = False
    ):
        """
        Create a new DNS zone.

        Args:
            domain: Domain name to create

        Returns:
            Created zone dictionary
        """

        data = {
            "domain": parent_domain,
            "subdomain": subdomain,
            "project_id": self.project_id,
        }

        if pretend:
            logger.info(
                "Pretend: Would create zone %s with subdomain %s",
                parent_domain,
                subdomain,
            )
            return

        response = self._make_request("POST", "dns-zones", data)
        if "domain" not in response:
            raise ValueError(f"Failed to create zone {parent_domain}: {response}")

    def get_zones(self) -> List[Dict[str, Any]]:
        """
        Get all DNS zones.

        Returns:
            List of zone dictionaries
        """
        response = self._make_request("GET", "dns-zones", paginate=True)
        return response.get("dns_zones", [])

    def _sync_records(self, expected_records, current_records, zone_name, pretend):
        """
        Sync DNS records.
        """

        changes = []

        matching_current = set()
        matching_expected = set()
        for i, record in enumerate(current_records):
            for y, expected_record in enumerate(expected_records):
                if (
                    record["name"] == expected_record["target"]
                    and record["type"].upper() == expected_record["type"].upper()
                    and record["data"] == expected_record["value"]
                ):
                    matching_current.add(i)
                    matching_expected.add(y)
                    break

        # Remove records that are already in the zone.
        current_records = [
            record
            for i, record in enumerate(current_records)
            if i not in matching_current
        ]
        expected_records = [
            record
            for y, record in enumerate(expected_records)
            if y not in matching_expected
        ]

        # Delete records from the zone that conflict with the expected records.
        for record in current_records:
            patch = [
                {
                    "delete": {
                        "id_fields": {
                            "name": record["name"],
                            "type": record["type"].upper(),
                            "data": record["data"],
                        }
                    }
                }
            ]
            self._patch_records(zone_name, patch, pretend)
            changes.extend(patch)

        # Add records that are not in the zone.
        if len(expected_records) > 0:
            patch = [
                {
                    "add": {
                        "records": [
                            {
                                "name": record["target"],
                                "type": record["type"].upper(),
                                "data": record["value"],
                                "ttl": self.ttl,
                            }
                            for record in expected_records
                        ]
                    }
                }
            ]
            self._patch_records(zone_name, patch, pretend)
            changes.extend(patch)

        return changes

    def _patch_records(self, zone_name, changes, pretend):
        """
        Send a PATCH request to the Scaleway API to update the records.
        """

        if pretend:
            logger.info("Pretend: Would sync records for %s: %s", zone_name, changes)
        else:
            response = self._make_request(
                "PATCH", f"dns-zones/{zone_name}/records", {"changes": changes}
            )
            if "records" not in response:
                raise ValueError(f"Failed to sync records for {zone_name}: {response}")

        return changes
