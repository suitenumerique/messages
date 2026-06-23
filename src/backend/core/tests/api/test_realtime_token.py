"""Tests for the SSE connection-token endpoint (RealtimeTokenView).

When the feature is off it returns a null token (200) so clients fall back to
polling cleanly, and it must never hand out a token signed with an empty secret
(503) so a misconfiguration can't produce a forgeable credential.
"""

from django.test import override_settings
from django.urls import reverse

import jwt
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import factories

pytestmark = pytest.mark.django_db


def _url():
    return reverse("realtime-token")


def test_requires_authentication():
    resp = APIClient().post(_url())
    assert resp.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )


@override_settings(REALTIME_ENABLED=False, REALTIME_JWT_SECRET="s3cret")
def test_null_token_when_disabled():
    """Realtime off is a normal answer (200, null token) — not a 404/error —
    so the client falls back to polling instead of retry-storming."""
    client = APIClient()
    client.force_authenticate(user=factories.UserFactory())
    resp = client.post(_url())
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {"token": None}


@override_settings(REALTIME_ENABLED=True, REALTIME_JWT_SECRET="")
def test_503_when_secret_missing():
    client = APIClient()
    client.force_authenticate(user=factories.UserFactory())
    assert client.post(_url()).status_code == status.HTTP_503_SERVICE_UNAVAILABLE


@override_settings(
    REALTIME_ENABLED=True,
    REALTIME_JWT_SECRET="s3cret",
    REALTIME_JWT_ALGORITHM="HS256",
)
def test_mints_token_for_current_user():
    user = factories.UserFactory()
    client = APIClient()
    client.force_authenticate(user=user)
    resp = client.post(_url())
    assert resp.status_code == status.HTTP_200_OK
    claims = jwt.decode(resp.json()["token"], "s3cret", algorithms=["HS256"])
    assert claims["sub"] == str(user.id)
