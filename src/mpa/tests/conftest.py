"""Pytest configuration and fixtures for mpa tests."""

import logging
import os
import time

import pytest
import requests

logger = logging.getLogger(__name__)

RSPAMD_URL = os.getenv("RSPAMD_URL", "http://localhost:8010/_api")
RSPAMD_AUTH = os.getenv("RSPAMD_AUTH", "password")


@pytest.fixture(scope="session", autouse=True)
def wait_for_rspamd():
    """Wait for rspamd to be ready before running tests."""
    max_retries = 200  # Increase retries (40 seconds total)
    base_url = RSPAMD_URL.replace("/_api", "")
    last_error = None
    
    for attempt in range(max_retries):
        # Try checkv2 endpoint first (more reliable than ping through nginx)
        try:
            headers = {"Content-Type": "message/rfc822"}
            if RSPAMD_AUTH:
                headers["Authorization"] = RSPAMD_AUTH
            response = requests.post(
                f"{RSPAMD_URL}/checkv2",
                data=b"",
                headers=headers,
                timeout=3,
            )
            # If we get a response (even if it's an error about empty message), rspamd is up
            if response.status_code in (200, 400, 401, 403):
                logger.info(f"Rspamd is ready (checkv2 check returned {response.status_code})")
                return
            last_error = f"Unexpected status code: {response.status_code}"
        except requests.exceptions.ConnectionError as e:
            # Connection refused - service not up yet
            last_error = f"Connection error: {e}"
            pass
        except requests.exceptions.Timeout:
            last_error = "Request timeout"
            pass
        except requests.exceptions.RequestException as e:
            # Other errors - log but continue
            last_error = f"Request error: {e}"
            if attempt % 30 == 0:
                logger.debug(f"Checkv2 check error: {e}")
        
        # Also try ping endpoint as backup
        try:
            response = requests.get(f"{base_url}/ping", timeout=2)
            if response.status_code == 200 and response.text == "pong":
                logger.info("Rspamd is ready (ping check)")
                return
        except requests.exceptions.RequestException:
            # Ignore ping errors, we prefer checkv2
            pass
        
        if attempt == max_retries - 1:
            raise RuntimeError(
                f"Rspamd did not become ready after {max_retries} attempts. "
                f"Last error: {last_error}. "
                f"Tried {RSPAMD_URL}/checkv2 and {base_url}/ping"
            )
        if attempt % 30 == 0:
            logger.warning(
                f"Rspamd not ready yet (attempt {attempt + 1}/{max_retries}), "
                f"last error: {last_error}, retrying..."
            )
        time.sleep(0.2)

