"""
Scaleway DNS provider implementation.
"""

from typing import Any, Dict, List, Optional

from django.conf import settings

import requests


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

    def _get_zone_name(self, domain: str) -> str:
        """
        Get the zone name for API calls.

        Args:
            domain: Domain name

        Returns:
            Zone name for API calls
        """
        # First, check if the exact domain exists as a zone
        zone = self.get_zone(domain)
        if zone:
            return domain

        # If not found, check if we need to use a parent zone
        # This handles cases where the domain is a subdomain of an existing zone
        parts = domain.split(".")
        for i in range(1, len(parts)):
            potential_parent = ".".join(parts[i:])
            zone = self.get_zone(potential_parent)
            if zone:
                return potential_parent

        # If no parent found, use the domain as is
        return domain

    def _validate_zone_exists(self, domain: str) -> bool:
        """
        Validate that a zone exists for the given domain.

        Args:
            domain: Domain name

        Returns:
            True if zone exists, False otherwise
        """
        zone = self.get_zone(domain)
        return zone is not None

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

    def _make_request(
        self, method: str, endpoint: str, data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Make a request to the Scaleway API.

        Args:
            method: HTTP method
            endpoint: API endpoint
            data: Request data

        Returns:
            API response as dictionary

        Raises:
            Exception: If the request fails
        """
        url = f"{self.base_url}/{endpoint}"

        response = requests.request(
            method=method, url=url, headers=self.headers, json=data, timeout=30
        )

        if not response.ok:
            self._handle_api_error(response)

        return response.json()

    def get_zones(self) -> List[Dict[str, Any]]:
        """
        Get all DNS zones.

        Returns:
            List of zone dictionaries
        """
        response = self._make_request("GET", "dns-zones")
        return response.get("dns_zones", [])

    def get_zone(self, domain: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific DNS zone by domain name.

        Args:
            domain: Domain name

        Returns:
            Zone dictionary or None if not found
        """
        zones = self.get_zones()
        for zone in zones:
            # Check if this zone matches our domain
            zone_domain = zone.get("domain", "")
            zone_subdomain = zone.get("subdomain", "")

            if zone_subdomain:
                # This is a subdomain zone
                zone_full_name = f"{zone_subdomain}.{zone_domain}"
                if zone_full_name == domain:
                    return zone
            # This is a root domain zone
            elif zone_domain == domain:
                return zone
        return None

    def _resolve_zone_components(self, domain: str) -> tuple[str, str]:
        """
        Resolve domain into parent domain and subdomain components.

        This method implements a smart algorithm to determine the correct
        parent domain and subdomain by checking existing zones:
        - For x.tld: create new zone, no parent
        - For a.b.c.d.tld: recursively check potential parent zones starting with b.c.d.tld
        - If parent exists, create sub-zone; otherwise create new zone

        Args:
            domain: Domain name

        Returns:
            Tuple of (parent_domain, subdomain)
        """
        if "." not in domain:
            # Single level domain, no parent
            return domain, ""

        # Get existing zones to check for potential parents
        existing_zones = self.get_zones()

        # Split domain into parts
        parts = domain.split(".")

        # For domains like a.b.c.d.tld, check potential parents:
        # - b.c.d.tld
        # - c.d.tld
        # - d.tld
        # - tld (but this is unlikely to be a managed zone)

        for i in range(1, len(parts)):
            potential_parent = ".".join(parts[i:])

            # Check if this potential parent exists as a zone
            for zone in existing_zones:
                zone_domain = zone.get("domain", "")
                zone_subdomain = zone.get("subdomain", "")

                if zone_subdomain:
                    # This is a subdomain zone
                    zone_full_name = f"{zone_subdomain}.{zone_domain}"
                    if zone_full_name == potential_parent:
                        # Found existing parent zone, create sub-zone
                        # The subdomain should be the remaining parts
                        subdomain = ".".join(parts[:i])
                        return zone_domain, subdomain
                # This is a root domain zone
                elif zone_domain == potential_parent:
                    # Found existing parent zone, create sub-zone
                    # The subdomain should be the remaining parts
                    subdomain = ".".join(parts[:i])
                    return zone_domain, subdomain

        # No existing parent zone found, create new zone
        # Use the last two parts as the parent domain (common pattern)
        if len(parts) >= 2:
            parent_domain = ".".join(parts[-2:])
            subdomain = ".".join(parts[:-2])
        else:
            parent_domain = domain
            subdomain = ""

        return parent_domain, subdomain

    def create_zone(self, domain: str) -> Dict[str, Any]:
        """
        Create a new DNS zone.

        Args:
            domain: Domain name to create

        Returns:
            Created zone dictionary
        """
        parent_domain, subdomain = self._resolve_zone_components(domain)

        data = {
            "domain": parent_domain,
            "subdomain": subdomain,
            "project_id": self.project_id,
        }

        response = self._make_request("POST", "dns-zones", data)
        return response.get("dns_zone", {})

    def get_records(self, domain: str) -> List[Dict[str, Any]]:
        """
        Get all DNS records for a zone.

        Args:
            domain: Domain name

        Returns:
            List of record dictionaries
        """
        zone_name = self._get_zone_name(domain)
        response = self._make_request("GET", f"dns-zones/{zone_name}/records")
        return response.get("records", [])

    def _format_record_name(self, name: str, domain: str) -> str:
        """
        Format record name according to Scaleway API requirements.

        Args:
            name: Record name (can be FQDN or short name)
            domain: Domain name

        Returns:
            Short format record name
        """
        # If name is empty or None, return empty string
        if not name:
            return ""

        # If name is exactly the domain, it's a root domain record
        if name == domain:
            return ""

        # If name is FQDN ending with domain, extract short name
        if name.endswith(f".{domain}"):
            # Remove the domain suffix to get the short name
            short_name = name[: -len(f".{domain}")]
            # If the result is empty, it means this is a root domain record
            return short_name if short_name else ""

        # If name contains dots but doesn't end with domain, it might be a subdomain
        # Return the first part as short name
        if "." in name:
            return name.split(".")[0]

        # Otherwise, return as is (already short format)
        return name

    def create_record(
        self,
        domain: str,
        name: str,
        record_type: str,
        data: str,
        ttl: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Create a DNS record.

        Args:
            domain: Domain name
            name: Record name
            record_type: Record type (A, MX, TXT, etc.)
            data: Record data
            ttl: TTL in seconds (uses DNS_SCALEWAY_TTL if not specified)

        Returns:
            Created record dictionary
        """
        if ttl is None:
            ttl = self.ttl

        zone_name = self._get_zone_name(domain)

        # Validate that the zone exists
        if not self._validate_zone_exists(zone_name):
            raise ValueError(f"Zone not found: {zone_name}")

        short_name = self._format_record_name(name, domain)

        record_data = {
            "return_all_records": False,
            "changes": [
                {
                    "add": {
                        "records": [
                            {
                                "name": short_name,
                                "type": record_type,
                                "data": data,
                                "ttl": ttl,
                            }
                        ]
                    }
                }
            ],
        }

        response = self._make_request(
            "PATCH", f"dns-zones/{zone_name}/records", record_data
        )
        return response.get("records", [{}])[0] if response.get("records") else {}

    def update_record(
        self,
        domain: str,
        record_id: str,  # pylint: disable=unused-argument
        name: str,
        record_type: str,
        data: str,
        ttl: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Update a DNS record.

        Args:
            domain: Domain name
            record_id: Record ID (not used, kept for compatibility)
            name: Record name
            record_type: Record type
            data: Record data
            ttl: TTL in seconds (uses DNS_SCALEWAY_TTL if not specified)

        Returns:
            Updated record dictionary
        """
        if ttl is None:
            ttl = self.ttl

        zone_name = self._get_zone_name(domain)

        # Validate that the zone exists
        if not self._validate_zone_exists(zone_name):
            raise ValueError(f"Zone not found: {zone_name}")

        short_name = self._format_record_name(name, domain)

        record_data = {
            "return_all_records": False,
            "changes": [
                {
                    "set": {
                        "id_fields": {
                            "name": short_name,
                            "type": record_type,
                        },
                        "records": [
                            {
                                "name": short_name,
                                "type": record_type,
                                "data": data,
                                "ttl": ttl,
                            }
                        ],
                    }
                }
            ],
        }

        response = self._make_request(
            "PATCH", f"dns-zones/{zone_name}/records", record_data
        )
        return response.get("records", [{}])[0] if response.get("records") else {}

    def delete_record(self, domain: str, record_id: str) -> None:
        """
        Delete a DNS record.

        Args:
            domain: Domain name
            record_id: Record ID (not used, kept for compatibility)
        """
        # For delete operations, we need to specify the record by name and type
        # Since we don't have the name and type here, this method needs to be updated
        # to accept name and type parameters
        raise NotImplementedError(
            "Delete record requires name and type parameters. Use delete_record_by_name_type instead."
        )

    def delete_record_by_name_type(
        self, domain: str, name: str, record_type: str
    ) -> None:
        """
        Delete a DNS record by name and type.

        Args:
            domain: Domain name
            name: Record name
            record_type: Record type
        """
        zone_name = self._get_zone_name(domain)

        # Validate that the zone exists
        if not self._validate_zone_exists(zone_name):
            raise ValueError(f"Zone not found: {zone_name}")

        short_name = self._format_record_name(name, domain)

        record_data = {
            "return_all_records": False,
            "changes": [
                {
                    "delete": {
                        "id_fields": {
                            "name": short_name,
                            "type": record_type,
                        }
                    }
                }
            ],
        }

        self._make_request("PATCH", f"dns-zones/{zone_name}/records", record_data)

    def find_records(
        self, domain: str, name: str, record_type: str
    ) -> List[Dict[str, Any]]:
        """
        Find all DNS records of a specific type and name.

        Args:
            domain: Domain name
            name: Record name
            record_type: Record type

        Returns:
            List of record dictionaries
        """
        records = self.get_records(domain)
        found_records = []
        short_name = self._format_record_name(name, domain)

        for record in records:
            if record.get("name") == short_name and record.get("type") == record_type:
                found_records.append(record)
        return found_records

    def find_record(
        self, domain: str, name: str, record_type: str
    ) -> Optional[Dict[str, Any]]:
        """
        Find a specific DNS record.

        Args:
            domain: Domain name
            name: Record name
            record_type: Record type

        Returns:
            Record dictionary or None if not found
        """
        records = self.find_records(domain, name, record_type)
        return records[0] if records else None

    def record_exists(
        self, domain: str, name: str, record_type: str, expected_value: str
    ) -> bool:
        """
        Check if a specific DNS record with the expected value exists.

        Args:
            domain: Domain name
            name: Record name
            record_type: Record type
            expected_value: Expected record value

        Returns:
            True if the record exists with the expected value
        """
        records = self.find_records(domain, name, record_type)
        for record in records:
            if record.get("data") == expected_value:
                return True
        return False

    def provision_domain_records(
        self, domain: str, expected_records: List[Dict[str, Any]], pretend: bool = False
    ) -> Dict[str, Any]:
        """
        Provision DNS records for a domain.
        Only creates records that don't already exist.

        Args:
            domain: Domain name
            expected_records: List of expected DNS records
            pretend: If True, simulate operations without making actual changes

        Returns:
            Dictionary with provisioning results
        """
        # Get or create zone
        zone = self.get_zone(domain)
        if not zone:
            if pretend:
                # Simulate zone creation
                zone = {"domain": domain}
            else:
                try:
                    zone = self.create_zone(domain)
                except Exception as e:  # pylint: disable=broad-exception-caught
                    return {
                        "success": False,
                        "error": f"Failed to create zone for {domain}: {e}",
                        "domain": domain,
                    }

        # Use domain name directly for API calls
        zone_name = self._get_zone_name(domain)
        results = {
            "success": True,
            "domain": domain,
            "zone_name": zone_name,
            "created": [],
            "updated": [],
            "errors": [],
            "pretend": pretend,
        }

        # Provision each expected record
        for expected_record in expected_records:
            record_type = expected_record["type"]
            target = expected_record["target"]
            expected_value = expected_record["value"]

            # Build record name
            if target:
                record_name = f"{target}.{domain}"
            else:
                record_name = domain

            try:
                # Check if this specific record already exists
                if self.record_exists(domain, record_name, record_type, expected_value):
                    # Record already exists, skip it
                    continue

                if pretend:
                    # Simulate creating new record
                    results["created"].append(
                        {
                            "name": record_name,
                            "type": record_type,
                            "value": expected_value,
                            "pretend": True,
                        }
                    )
                else:
                    # Create new record only if it doesn't exist
                    self.create_record(domain, record_name, record_type, expected_value)
                    results["created"].append(
                        {
                            "name": record_name,
                            "type": record_type,
                            "value": expected_value,
                        }
                    )

            except Exception as e:  # pylint: disable=broad-exception-caught
                error_data = {
                    "name": record_name,
                    "type": record_type,
                    "value": expected_value,
                    "error": str(e),
                }
                if pretend:
                    error_data["pretend"] = True
                results["errors"].append(error_data)
                results["success"] = False

        return results
