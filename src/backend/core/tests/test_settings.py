"""
Unit tests for the User model
"""

import pytest

from messages.settings import Base


def test_invalid_settings_oidc_email_configuration():
    """
    The OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION and OIDC_ALLOW_DUPLICATE_EMAILS settings
    should not be both set to True simultaneously.
    """

    class TestSettings(Base):
        """Fake test settings."""

        OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION = True
        OIDC_ALLOW_DUPLICATE_EMAILS = True

    # The validation is performed during post_setup
    with pytest.raises(ValueError) as excinfo:
        TestSettings().post_setup()

    # Check the exception message
    assert str(excinfo.value) == (
        "Both OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION and "
        "OIDC_ALLOW_DUPLICATE_EMAILS cannot be set to True simultaneously. "
    )


def test_invalid_settings_oidc_refresh_token_configuration():
    """
    The OIDC_STORE_REFRESH_TOKEN_KEY setting must be set when
    OIDC_STORE_REFRESH_TOKEN is enabled.
    """

    class TestSettings(Base):
        """Fake test settings."""

        OIDC_STORE_REFRESH_TOKEN = True
        OIDC_STORE_REFRESH_TOKEN_KEY = None

    # The validation is performed during post_setup
    with pytest.raises(ValueError) as excinfo:
        TestSettings().post_setup()

    # Check the exception message
    assert str(excinfo.value) == (
        "OIDC_STORE_REFRESH_TOKEN_KEY must be set when "
        "OIDC_STORE_REFRESH_TOKEN is enabled."
    )


def test_web_push_public_key_without_private_key_is_rejected():
    """A VAPID public key set without the private key must fail at boot.

    /config would advertise the public key and enrol browsers, yet every send
    would silently no-op (the web sender needs the private key to sign). The
    validation is symmetric, so this half-configuration is caught like the
    reverse one.
    """

    class TestSettings(Base):
        """Fake test settings."""

        PUSH_ENABLED = True
        PUSH_VAPID_PRIVATE_KEY = None
        PUSH_VAPID_PUBLIC_KEY = "public-key"
        PUSH_VAPID_SUBJECT = "mailto:ops@example.com"

    with pytest.raises(ValueError) as excinfo:
        TestSettings().post_setup()

    message = str(excinfo.value)
    assert "partially configured" in message
    assert "PUSH_VAPID_PRIVATE_KEY missing" in message


def test_web_push_private_key_without_public_key_is_rejected():
    """A VAPID private key set without the public key must fail at boot.

    The browser needs the public key as its applicationServerKey to subscribe;
    without it, nobody can enrol.
    """

    class TestSettings(Base):
        """Fake test settings."""

        PUSH_ENABLED = True
        PUSH_VAPID_PRIVATE_KEY = "private-key"
        PUSH_VAPID_PUBLIC_KEY = None
        PUSH_VAPID_SUBJECT = "mailto:ops@example.com"

    with pytest.raises(ValueError) as excinfo:
        TestSettings().post_setup()

    assert "PUSH_VAPID_PUBLIC_KEY missing" in str(excinfo.value)


def test_web_push_native_only_deployment_needs_no_vapid():
    """PUSH_ENABLED with no VAPID value at all is valid (native-only APNs/FCM).

    PUSH_ENABLED alone must never force VAPID onto an instance that only ships
    native push.
    """

    class TestSettings(Base):
        """Fake test settings."""

        PUSH_ENABLED = True
        PUSH_VAPID_PRIVATE_KEY = None
        PUSH_VAPID_PUBLIC_KEY = None
        PUSH_VAPID_SUBJECT = None

    # Does not raise.
    TestSettings().post_setup()


def test_apns_partial_configuration_is_rejected():
    """An incomplete APNs group must fail at boot.

    The iOS sender gates on all four values (apns_configured), so a partial
    group silently drops every send even with PUSH_ENABLED True.
    """

    class TestSettings(Base):
        """Fake test settings."""

        PUSH_ENABLED = True
        PUSH_APNS_KEY = "apns-key"
        PUSH_APNS_KEY_ID = "key-id"
        PUSH_APNS_TEAM_ID = None
        PUSH_APNS_BUNDLE_ID = None

    with pytest.raises(ValueError) as excinfo:
        TestSettings().post_setup()

    message = str(excinfo.value)
    assert "APNs is partially configured" in message
    assert "PUSH_APNS_TEAM_ID" in message
    assert "PUSH_APNS_BUNDLE_ID" in message


def test_fcm_partial_configuration_is_rejected():
    """FCM credentials without the project id must fail at boot.

    The Android sender gates on both values (fcm_configured), so a partial
    group silently drops every send even with PUSH_ENABLED True.
    """

    class TestSettings(Base):
        """Fake test settings."""

        PUSH_ENABLED = True
        PUSH_FCM_CREDENTIALS = '{"type": "service_account"}'
        PUSH_FCM_PROJECT_ID = None

    with pytest.raises(ValueError) as excinfo:
        TestSettings().post_setup()

    assert "FCM is partially configured" in str(excinfo.value)
    assert "PUSH_FCM_PROJECT_ID missing" in str(excinfo.value)


def test_push_fully_configured_gateways_pass_validation():
    """Complete groups (any subset of gateways) boot fine with PUSH_ENABLED."""

    class TestSettings(Base):
        """Fake test settings."""

        PUSH_ENABLED = True
        PUSH_APNS_KEY = "apns-key"
        PUSH_APNS_KEY_ID = "key-id"
        PUSH_APNS_TEAM_ID = "team-id"
        PUSH_APNS_BUNDLE_ID = "com.example.app"
        PUSH_FCM_CREDENTIALS = '{"type": "service_account"}'
        PUSH_FCM_PROJECT_ID = "example-project"

    # Does not raise: APNs and FCM are complete, VAPID is fully unset.
    TestSettings().post_setup()


def test_web_push_subject_must_be_mailto_or_https():
    """A fully configured VAPID trio with a bare-email subject is rejected."""

    class TestSettings(Base):
        """Fake test settings."""

        PUSH_ENABLED = True
        PUSH_VAPID_PRIVATE_KEY = "private-key"
        PUSH_VAPID_PUBLIC_KEY = "public-key"
        PUSH_VAPID_SUBJECT = "ops@example.com"  # missing mailto:

    with pytest.raises(ValueError) as excinfo:
        TestSettings().post_setup()

    assert "RFC 8292" in str(excinfo.value)
