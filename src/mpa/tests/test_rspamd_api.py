"""Simple tests for rspamd API."""

import os

import pytest
import requests
from conftest import RSPAMD_URL, RSPAMD_AUTH

def test_rspamd_health(wait_for_rspamd):
    """Test that rspamd is running and accessible."""
    # Use the controller endpoint for health check
    base_url = RSPAMD_URL.replace("/_api", "")
    response = requests.get(f"{base_url}/ping", timeout=5)
    assert response.status_code == 200
    assert response.text.strip() == "pong"


def test_rspamd_check_empty_message(wait_for_rspamd):
    """Test rspamd checkv2 API with an empty message."""
    # Empty email message
    empty_email = b""
    
    headers = {"Content-Type": "message/rfc822"}
    if RSPAMD_AUTH:
        # RSPAMD_AUTH is used directly as Authorization header value
        headers["Authorization"] = RSPAMD_AUTH
    
    response = requests.post(
        f"{RSPAMD_URL}/checkv2",
        data=empty_email,
        headers=headers,
        timeout=10,
    )
    
    assert response.status_code == 200
    result = response.json()
    
    # Verify response structure
    assert "action" in result
    assert "score" in result
    assert "required_score" in result
    assert "is_skipped" in result
    
    # Empty message may be marked as spam depending on rspamd configuration
    # At minimum, verify we got a valid response with the expected structure
    assert result["action"] in ("reject", "add header", "greylist", "no action")
    assert isinstance(result["score"], (int, float))
    # required_score can be None in some rspamd configurations
    assert result["required_score"] is None or isinstance(result["required_score"], (int, float))


def test_rspamd_check_simple_message(wait_for_rspamd):
    """Test rspamd checkv2 API with a simple valid message."""
    # Simple valid email
    simple_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
Date: Mon, 1 Jan 2024 12:00:00 +0000

This is a test email body.
"""
    
    headers = {"Content-Type": "message/rfc822"}
    if RSPAMD_AUTH:
        # RSPAMD_AUTH is used directly as Authorization header value
        headers["Authorization"] = RSPAMD_AUTH
    
    response = requests.post(
        f"{RSPAMD_URL}/checkv2",
        data=simple_email,
        headers=headers,
        timeout=10,
    )
    
    assert response.status_code == 200
    result = response.json()
    
    # Verify response structure
    assert "action" in result
    assert "score" in result
    assert "required_score" in result
    
    # Simple valid message should not be rejected
    # If required_score is None, just check that action is not reject
    if result["required_score"] is not None:
        assert result["action"] != "reject"
    else:
        # If no required_score, just verify action is valid
        assert result["action"] in ("reject", "add header", "greylist", "no action")

