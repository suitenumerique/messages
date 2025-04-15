"""Tests for MTA API endpoints."""

import pytest
import jwt
import hashlib
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient

@pytest.fixture
def api_client():
    """Return an API client."""
    return APIClient()

@pytest.fixture
def sample_email():
    """Return a sample email in RFC822 format."""
    return b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email

This is a test email body.
"""

@pytest.fixture
def valid_jwt_token(sample_email):
    """Return a valid JWT token for the sample email."""
    email_hash = hashlib.sha256(sample_email).hexdigest()
    payload = {
        "email_hash": email_hash,
        "original_recipients": ["recipient@example.com"]
    }
    return jwt.encode(payload, settings.MDA_API_SECRET, algorithm="HS256")

@pytest.mark.django_db
class TestMTAIncomingMail:
    """Test the MTA incoming mail endpoint."""

    def test_valid_email_submission(self, api_client, sample_email, valid_jwt_token):
        """Test submitting a valid email with correct JWT token."""
        response = api_client.post(
            "/api/v1.0/mta/incoming_mail/",
            data=sample_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {valid_jwt_token}"
        )
        
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok"}

    def test_invalid_content_type(self, api_client, sample_email, valid_jwt_token):
        """Test submitting with wrong content type."""
        response = api_client.post(
            "/api/v1.0/mta/incoming_mail/",
            data=sample_email,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {valid_jwt_token}"
        )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Content-Type must be message/rfc822" in response.json()["detail"]

    def test_missing_auth_header(self, api_client, sample_email):
        """Test submitting without authorization header."""
        response = api_client.post(
            "/api/v1.0/mta/incoming_mail/",
            data=sample_email,
            content_type="message/rfc822"
        )
        
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Authorization header missing" in response.json()["detail"]

    def test_invalid_jwt_token(self, api_client, sample_email):
        """Test submitting with invalid JWT token."""
        response = api_client.post(
            "/api/v1.0/mta/incoming_mail/",
            data=sample_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION="Bearer invalid_token"
        )
        
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_mismatched_email_hash(self, api_client, sample_email):
        """Test submitting with JWT token containing wrong email hash."""
        wrong_payload = {
            "email_hash": "wrong_hash",
            "original_recipients": ["recipient@example.com"]
        }
        wrong_token = jwt.encode(
            wrong_payload, 
            settings.MDA_API_SECRET, 
            algorithm="HS256"
        )
        
        response = api_client.post(
            "/api/v1.0/mta/incoming_mail/",
            data=sample_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {wrong_token}"
        )
        
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Invalid email hash" in response.json()["detail"] 