"""Tests for MTA API endpoints."""

import hashlib

from django.conf import settings

import jwt
import pytest
from rest_framework import status
from rest_framework.test import APIClient


@pytest.fixture(name="api_client")
def fixture_api_client():
    """Return an API client."""
    return APIClient()


@pytest.fixture(name="sample_email")
def fixture_sample_email():
    """Return a sample email in RFC822 format."""
    return b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email

This is a test email body.
"""


@pytest.fixture(name="valid_jwt_token")
def fixture_valid_jwt_token(sample_email):
    """Return a valid JWT token for the sample email."""
    body_hash = hashlib.sha256(sample_email).hexdigest()
    payload = {"body_hash": body_hash, "original_recipients": ["recipient@example.com"]}
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
            HTTP_AUTHORIZATION=f"Bearer {valid_jwt_token}",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok"}

    def test_invalid_content_type(self, api_client, sample_email, valid_jwt_token):
        """Test submitting with wrong content type."""
        response = api_client.post(
            "/api/v1.0/mta/incoming_mail/",
            data=sample_email,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {valid_jwt_token}",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_auth_header(self, api_client, sample_email):
        """Test submitting without authorization header."""
        response = api_client.post(
            "/api/v1.0/mta/incoming_mail/",
            data=sample_email,
            content_type="message/rfc822",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalid_jwt_token(self, api_client, sample_email):
        """Test submitting with invalid JWT token."""
        response = api_client.post(
            "/api/v1.0/mta/incoming_mail/",
            data=sample_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION="Bearer invalid_token",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_mismatched_body_hash(self, api_client, sample_email):
        """Test submitting with JWT token containing wrong email hash."""
        wrong_payload = {
            "body_hash": "wrong_hash",
            "original_recipients": ["recipient@example.com"],
        }
        wrong_token = jwt.encode(
            wrong_payload, settings.MDA_API_SECRET, algorithm="HS256"
        )

        response = api_client.post(
            "/api/v1.0/mta/incoming_mail/",
            data=sample_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {wrong_token}",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
