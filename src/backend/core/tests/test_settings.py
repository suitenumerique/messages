"""
Unit tests for the User model
"""

import os
from unittest.mock import patch

import pytest

from messages import settings as settings_module
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


class TestRelayTlsLevelSplit:
    """MTA_OUT_RELAY_TLS_SECURITY_LEVEL was split out of the direct-MX one.

    The old value is not inherited — the two legs face different peers and are
    configured apart. An install that had hardened the old setting is told at
    startup, so the split is announced rather than found in a packet capture.
    """

    LEVELS = ("MTA_OUT_SMTP_TLS_SECURITY_LEVEL", "MTA_OUT_RELAY_TLS_SECURITY_LEVEL")

    @classmethod
    def _env(cls, **overrides):
        """Both names dropped before applying overrides.

        The dev compose file pins MTA_OUT_RELAY_TLS_SECURITY_LEVEL, and the
        warning keys off the variable being absent — left in place it reads as
        a deliberate operator choice and every case below passes for the wrong
        reason.
        """
        env = {k: v for k, v in os.environ.items() if k not in cls.LEVELS}
        env.update(overrides)
        return patch.dict("os.environ", env, clear=True)

    @staticmethod
    def _settings(smtp_level="may", relay_level="may"):
        class TestSettings(Base):
            """Fake test settings."""

            MTA_OUT_SMTP_TLS_SECURITY_LEVEL = smtp_level
            MTA_OUT_RELAY_TLS_SECURITY_LEVEL = relay_level

        return TestSettings()

    def test_old_value_is_not_inherited(self):
        """The relay leg keeps its own default, whatever the old name says."""
        with self._env(MTA_OUT_SMTP_TLS_SECURITY_LEVEL="secure"):
            with patch.object(settings_module, "logger"):
                settings = self._settings(smtp_level="secure")

        assert settings.MTA_OUT_RELAY_TLS_SECURITY_LEVEL == "may"

    def test_a_hardened_old_value_warns(self):
        """Silence here is what let a hardened relay quietly drop to CERT_NONE."""
        with self._env(MTA_OUT_SMTP_TLS_SECURITY_LEVEL="secure"):
            with patch.object(settings_module, "logger") as mock_logger:
                self._settings(smtp_level="secure")

        assert mock_logger.warning.call_count == 1
        message, *args = mock_logger.warning.call_args[0]
        assert "MTA_OUT_RELAY_TLS_SECURITY_LEVEL" in message
        assert "secure" in args

    def test_matching_levels_stay_quiet(self):
        """The common case — both on the default — is not worth a warning."""
        with self._env(MTA_OUT_SMTP_TLS_SECURITY_LEVEL="may"):
            with patch.object(settings_module, "logger") as mock_logger:
                self._settings(smtp_level="may")

        mock_logger.warning.assert_not_called()

    def test_explicit_new_value_stays_quiet(self):
        """An operator who set the new name has already made the choice."""
        with self._env(
            MTA_OUT_SMTP_TLS_SECURITY_LEVEL="secure",
            MTA_OUT_RELAY_TLS_SECURITY_LEVEL="may",
        ):
            with patch.object(settings_module, "logger") as mock_logger:
                settings = self._settings(smtp_level="secure")

        assert settings.MTA_OUT_RELAY_TLS_SECURITY_LEVEL == "may"
        mock_logger.warning.assert_not_called()

    def test_neither_set_keeps_the_default(self):
        with self._env():
            settings = self._settings()

        assert settings.MTA_OUT_RELAY_TLS_SECURITY_LEVEL == "may"

    def test_relay_level_is_validated_on_its_own(self):
        """The relay entry in the validation loop is reachable.

        With a valid SMTP level the loop passes its first entry, so only the
        relay entry can raise — otherwise deleting that entry would leave the
        suite green while a typo silently downgraded the relay leg to
        CERT_NONE.
        """
        with self._env(
            MTA_OUT_SMTP_TLS_SECURITY_LEVEL="may",
            MTA_OUT_RELAY_TLS_SECURITY_LEVEL="Secure",
        ):
            with pytest.raises(ValueError) as excinfo:
                self._settings(smtp_level="may", relay_level="Secure")

        assert "MTA_OUT_RELAY_TLS_SECURITY_LEVEL" in str(excinfo.value)
