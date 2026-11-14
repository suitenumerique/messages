"""
Unit tests for the User model
"""

import os
from unittest.mock import patch

import pytest

from core.enums import ThreadingSenderAuth, TrashbinAllowEmpty
from core.utils import StrEnumValue

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


@pytest.mark.parametrize("backend", ["bogus", "System", ""])
def test_unknown_dns_resolver_backend_fails_at_startup(backend):
    """A typo must stop startup rather than silently pick a backend."""

    class TestSettings(Base):
        """Fake test settings."""

        DNS_RESOLVER_BACKEND = backend

    with pytest.raises(ValueError, match="Invalid DNS_RESOLVER_BACKEND"):
        TestSettings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DNS_RESOLVER_TIMEOUT", 0.0),
        ("DNS_RESOLVER_MAX_RESOLUTION_TIME", -1.0),
        ("DNS_RESOLVER_INBOUND_AUTH_BUDGET", 0.0),
        ("DNS_RESOLVER_CACHE_MAX_TTL", -1),
        ("DNS_RESOLVER_CACHE_NEGATIVE_TTL", -5),
    ],
)
def test_dns_resolver_bounds_are_validated(name, value):
    """A zero timeout would time every lookup out."""
    test_settings = type("TestSettings", (Base,), {name: value})
    with pytest.raises(ValueError, match=name):
        test_settings()


def test_require_dnssec_needs_the_recursive_backend():
    """The system backend never validates, so every self-check would fail."""

    class TestSettings(Base):
        """Fake test settings."""

        DNS_RESOLVER_BACKEND = "system"
        MESSAGES_DKIM_VERIFY_OUTGOING_REQUIRE_DNSSEC = True

    with pytest.raises(ValueError, match="REQUIRE_DNSSEC"):
        TestSettings()

    class RecursiveSettings(Base):
        """Fake test settings."""

        DNS_RESOLVER_BACKEND = "recursive"
        MESSAGES_DKIM_VERIFY_OUTGOING_REQUIRE_DNSSEC = True

    RecursiveSettings()


@pytest.mark.parametrize("backend", ["system", "recursive"])
def test_known_dns_resolver_backends_are_accepted(backend):
    class TestSettings(Base):
        """Fake test settings."""

        DNS_RESOLVER_BACKEND = backend

    TestSettings()


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


def _trashbin_allow_empty():
    """Declare TRASHBIN_ALLOW_EMPTY as the settings do, reading the env."""
    return StrEnumValue(
        TrashbinAllowEmpty,
        TrashbinAllowEmpty.ADMIN,
        environ_name="TRASHBIN_ALLOW_EMPTY",
        environ_prefix=None,
    )


@pytest.mark.parametrize("policy", ["admins", "Admin", "everyone", ""])
def test_unknown_trashbin_allow_empty_fails_at_startup(monkeypatch, policy):
    """An unrecognised TRASHBIN_ALLOW_EMPTY stops the settings from loading.

    Matching no policy would otherwise collapse to "nobody may empty" — the
    same behaviour as a deliberate ``never``, with nothing to say the
    deployment is misconfigured. "admins" is the former plural value.
    """
    monkeypatch.setenv("TRASHBIN_ALLOW_EMPTY", policy)

    with pytest.raises(ValueError, match="expected one of never, admin, editor"):
        _trashbin_allow_empty()


def test_trashbin_allow_empty_is_parsed_into_the_enum(monkeypatch):
    """A valid value is stored as its enum member."""
    monkeypatch.setenv("TRASHBIN_ALLOW_EMPTY", "editor")

    assert _trashbin_allow_empty() is TrashbinAllowEmpty.EDITOR


@pytest.mark.parametrize("level", ["none", "Verified", "fail", ""])
def test_unknown_threading_min_sender_auth_fails_at_startup(monkeypatch, level):
    """An unrecognised level stops the settings from loading."""
    monkeypatch.setenv("MESSAGES_THREADING_MIN_SENDER_AUTH", level)

    with pytest.raises(ValueError, match="expected one of forged, unverified"):
        StrEnumValue(
            ThreadingSenderAuth,
            ThreadingSenderAuth.UNVERIFIED,
            environ_name="MESSAGES_THREADING_MIN_SENDER_AUTH",
            environ_prefix=None,
        )


def test_threading_min_sender_auth_default(settings):
    """Shipped default: forged mail never joins a thread, unverified does."""
    assert settings.MESSAGES_THREADING_MIN_SENDER_AUTH is (
        ThreadingSenderAuth.UNVERIFIED
    )


def test_trashbin_allow_empty_defaults_to_admin(monkeypatch):
    """Unset, only mailbox admins may empty the trashbin."""
    monkeypatch.delenv("TRASHBIN_ALLOW_EMPTY", raising=False)

    assert _trashbin_allow_empty() is TrashbinAllowEmpty.ADMIN


class TestRelayTlsLevelSplit:
    """MTA_OUT_RELAY_TLS_SECURITY_LEVEL was split out of the direct-MX one.

    Until it is set, the relay keeps the old value (no silent downgrade of a
    hardened relay), with a deprecation warning when the two differ.
    """

    LEVELS = ("MTA_OUT_SMTP_TLS_SECURITY_LEVEL", "MTA_OUT_RELAY_TLS_SECURITY_LEVEL")

    @classmethod
    def _env(cls, **overrides):
        """Both names dropped before applying overrides.

        ``deploy/env/backend.defaults`` pins the relay level, and the warning
        keys off the variable being absent.
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

    def test_old_value_is_inherited(self):
        """A relay hardened through the old name stays hardened on upgrade."""
        with self._env(MTA_OUT_SMTP_TLS_SECURITY_LEVEL="secure"):
            with patch.object(settings_module, "logger"):
                settings = self._settings(smtp_level="secure")

        assert settings.MTA_OUT_RELAY_TLS_SECURITY_LEVEL == "secure"

    def test_inherited_value_is_validated(self):
        with self._env(MTA_OUT_SMTP_TLS_SECURITY_LEVEL="Secure"):
            with pytest.raises(ValueError, match="TLS_SECURITY_LEVEL"):
                self._settings(smtp_level="Secure")

    def test_a_customized_old_value_warns(self):
        with self._env(MTA_OUT_SMTP_TLS_SECURITY_LEVEL="secure"):
            with patch.object(settings_module, "logger") as mock_logger:
                self._settings(smtp_level="secure")

        assert mock_logger.warning.call_count == 1
        message, *args = mock_logger.warning.call_args[0]
        assert "MTA_OUT_RELAY_TLS_SECURITY_LEVEL" in message
        assert "secure" in args

    def test_matching_levels_stay_quiet(self):
        with self._env(MTA_OUT_SMTP_TLS_SECURITY_LEVEL="may"):
            with patch.object(settings_module, "logger") as mock_logger:
                self._settings(smtp_level="may")

        mock_logger.warning.assert_not_called()

    def test_explicit_new_value_stays_quiet(self):
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
        """With a valid direct level, only the relay entry can raise."""
        with self._env(
            MTA_OUT_SMTP_TLS_SECURITY_LEVEL="may",
            MTA_OUT_RELAY_TLS_SECURITY_LEVEL="Secure",
        ):
            with pytest.raises(ValueError) as excinfo:
                self._settings(smtp_level="may", relay_level="Secure")

        assert "MTA_OUT_RELAY_TLS_SECURITY_LEVEL" in str(excinfo.value)


@pytest.mark.django_db
def test_relay_send_uses_the_relay_tls_level(settings):
    """The relay leg reads its own level, not the direct-MX one."""
    # pylint: disable=import-outside-toplevel
    from core.mda.outbound import send_outbound_email

    settings.MTA_OUT_MODE = "relay"
    settings.MTA_OUT_RELAY_HOST = "relay.test:587"
    settings.MTA_OUT_SMTP_TLS_SECURITY_LEVEL = "may"
    settings.MTA_OUT_RELAY_TLS_SECURITY_LEVEL = "secure"
    with patch("core.mda.outbound.send_smtp_mail", return_value={}) as mock_send:
        send_outbound_email({"to@example.com"}, "from@example.com", b"raw", {})
    assert mock_send.call_args.kwargs["smtp_tls_security_level"] == "secure"
