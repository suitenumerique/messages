"""Tests for device-encrypted push notification previews.

Covers native key registration, the per-platform rules deciding whether a device
may receive content, the sealed preview itself (decrypted here with the device
private key, as the iOS / Android app does), and how each sender carries it.
Gateways are mocked; the cryptography is real.
"""
# pylint: disable=protected-access

import json
import os
from datetime import timedelta
from unittest import mock

from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

import http_ece
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models
from core.enums import ChannelTypes, PushPlatformChoices
from core.services import push
from core.services.push import preview

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class Device:
    """A device key pair, generated the way the native app does."""

    def __init__(self):
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        public = self.private_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        self.keys = {
            "p256dh": preview.b64url_encode(public),
            "auth": preview.b64url_encode(self.auth),
        }

    def open(self, enc: str) -> bytes:
        """Decrypt a sealed preview, as the Notification Service Extension does."""
        return http_ece.decrypt(
            preview.b64url_decode(enc),
            private_key=self.private_key,
            auth_secret=self.auth,
            version="aes128gcm",
        )


def _message(subject="Quarterly report", sender_name="Alice", sender_email=None):
    """A message in a thread ``user`` can read, with the given sender/subject."""
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=enums.MailboxRoleChoices.EDITOR
    )
    thread = factories.ThreadFactory(messaged_at=timezone.now())
    factories.ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    sender = factories.ContactFactory(
        name=sender_name,
        email=sender_email or "alice@example.com",
        mailbox=mailbox,
    )
    message = factories.MessageFactory(thread=thread, subject=subject, sender=sender)
    return user, message


def _channel(user, platform=PushPlatformChoices.APNS, keys=None, **kwargs):
    channel, _ = push.register_push_device(
        user=user, platform=platform, token=f"tok-{platform}", keys=keys, **kwargs
    )
    return channel


def _thin(message):
    return push.build_thin_payload(message, unread_count=1)


@pytest.fixture(autouse=True)
def _previews_on(settings):
    settings.PUSH_PREVIEWS_ENABLED = True
    settings.PUSH_PREVIEW_MAX_IDLE_DAYS = 14


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestKeysRegistration:
    """Native apps may register a key pair; browsers stamp their session expiry."""

    @pytest.fixture(autouse=True)
    def _configure(self, settings):
        settings.PUSH_ENABLED = True
        settings.PUSH_APNS_KEY = "k"
        settings.PUSH_APNS_KEY_ID = "KEYID"
        settings.PUSH_APNS_TEAM_ID = "TEAM"
        settings.PUSH_APNS_BUNDLE_ID = "com.example.app"
        settings.PUSH_VAPID_PRIVATE_KEY = "vapid-key"
        settings.PUSH_VAPID_SUBJECT = "mailto:ops@example.com"

    def _register(self, client, **body):
        return client.post(
            reverse("user-channels-list"), {"type": "push", **body}, format="json"
        )

    def _client(self, user):
        client = APIClient()
        client.force_login(user)
        return client

    def test_native_keys_are_stored(self):
        """A native app's key pair is kept (encrypted) so previews can be sealed
        for it — it used to be silently dropped."""
        user = factories.UserFactory()
        keys = Device().keys
        resp = self._register(self._client(user), platform="apns", token="t", keys=keys)
        assert resp.status_code == status.HTTP_201_CREATED
        channel = models.Channel.objects.get(type=ChannelTypes.PUSH, user=user)
        assert channel.encrypted_settings["keys"] == keys
        # Native devices are not bound to the session lifetime.
        assert "session_expires_at" not in channel.settings

    def test_native_keys_are_optional(self):
        """An app that predates previews registers without keys."""
        user = factories.UserFactory()
        resp = self._register(self._client(user), platform="apns", token="t")
        assert resp.status_code == status.HTTP_201_CREATED
        channel = models.Channel.objects.get(type=ChannelTypes.PUSH, user=user)
        assert "keys" not in channel.encrypted_settings

    @pytest.mark.parametrize(
        "keys",
        [
            # Compressed-length point.
            {"p256dh": preview.b64url_encode(b"\x02" + b"x" * 32), "auth": "AAAA"},
            # Right length, but not a point on the curve.
            {
                "p256dh": preview.b64url_encode(b"\x04" + b"x" * 64),
                "auth": preview.b64url_encode(b"y" * 16),
            },
            # Auth secret of the wrong size.
            {"p256dh": Device().keys["p256dh"], "auth": preview.b64url_encode(b"y")},
            # Not base64url at all.
            {"p256dh": "%%%", "auth": "%%%"},
        ],
    )
    def test_invalid_native_keys_are_rejected(self, keys):
        """Our own app generates the keys, so a malformed pair is a bug worth a
        400 rather than previews that silently never decrypt."""
        user = factories.UserFactory()
        resp = self._register(self._client(user), platform="apns", token="t", keys=keys)
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "keys" in resp.json()
        assert not models.Channel.objects.filter(user=user).exists()

    def test_web_registration_stamps_session_expiry(self, settings):
        """A browser's previews are bounded by the registering session."""
        user = factories.UserFactory()
        before = timezone.now()
        resp = self._register(
            self._client(user),
            platform="web",
            token="https://push.example/ep",
            keys=Device().keys,
        )
        assert resp.status_code == status.HTTP_201_CREATED
        channel = models.Channel.objects.get(type=ChannelTypes.PUSH, user=user)
        expires_at = parse_datetime(channel.settings["session_expires_at"])
        assert (
            before
            < expires_at
            <= timezone.now() + timedelta(seconds=settings.SESSION_COOKIE_AGE)
        )


class TestSessionExpiresAt:
    """The real expiry of the registering session."""

    def test_without_session(self):
        """Token-authenticated callers have no session to bound previews."""
        assert preview.session_expires_at(None) is None
        assert preview.session_expires_at(mock.Mock(session_key=None)) is None

    def test_prefers_the_cache_ttl(self):
        """The cache backend expires a session ``age`` after its last save, so
        the backing cache TTL beats ``get_expiry_date()`` (now + age)."""
        session = mock.Mock(session_key="s", cache_key="sess:s")
        cache = mock.Mock()
        cache.ttl.return_value = 600
        with mock.patch.object(preview, "caches", {"default": cache, "session": cache}):
            expires_at = preview.session_expires_at(session)
        cache.ttl.assert_called_once_with("sess:s")
        assert abs((expires_at - timezone.now()) - timedelta(seconds=600)) < timedelta(
            seconds=5
        )
        session.get_expiry_date.assert_not_called()

    def test_falls_back_when_the_cache_fails(self):
        """A cache outage must not fail the device registration."""
        fallback = timezone.now() + timedelta(hours=1)
        session = mock.Mock(session_key="s", cache_key="sess:s")
        session.get_expiry_date.return_value = fallback
        cache = mock.Mock()
        cache.ttl.side_effect = ConnectionError("redis down")
        with mock.patch.object(preview, "caches", {"default": cache, "session": cache}):
            assert preview.session_expires_at(session) == fallback

    @pytest.mark.parametrize("ttl", [0, None])
    def test_falls_back_without_usable_ttl(self, ttl):
        """No TTL (missing key, no expiry) falls back to the session's own date."""
        fallback = timezone.now() + timedelta(hours=1)
        session = mock.Mock(session_key="s", cache_key="sess:s")
        session.get_expiry_date.return_value = fallback
        cache = mock.Mock()
        cache.ttl.return_value = ttl
        with mock.patch.object(preview, "caches", {"default": cache, "session": cache}):
            assert preview.session_expires_at(session) == fallback


# ---------------------------------------------------------------------------
# Who may receive content
# ---------------------------------------------------------------------------


class TestPreviewAllowed:
    """Per-platform rules: session-bound browsers, recently-used phones."""

    def _web(self, expires_at):
        return _channel(
            factories.UserFactory(),
            platform=PushPlatformChoices.WEB,
            keys=Device().keys,
            session_expires_at=expires_at,
        )

    def test_web_while_session_alive(self):
        """A browser gets previews until its session expires."""
        assert preview.preview_allowed(self._web(timezone.now() + timedelta(hours=1)))

    def test_web_after_session_expiry(self):
        """A shared computer must not keep showing a former user's subjects."""
        assert not preview.preview_allowed(
            self._web(timezone.now() - timedelta(minutes=1))
        )

    def test_web_without_stamp(self):
        """A browser registered before the stamp existed waits for its next
        (on-load) re-registration."""
        assert not preview.preview_allowed(self._web(None))

    def test_native_recently_opened(self):
        """A phone whose app was opened recently gets previews."""
        assert preview.preview_allowed(
            _channel(factories.UserFactory(), keys=Device().keys)
        )

    def test_native_idle_too_long(self, settings):
        """A phone left unopened (lost?) falls back to generic banners."""
        channel = _channel(factories.UserFactory(), keys=Device().keys)
        models.Channel.objects.filter(id=channel.id).update(
            last_used_at=timezone.now() - timedelta(days=15)
        )
        channel.refresh_from_db()
        assert not preview.preview_allowed(channel)
        settings.PUSH_PREVIEW_MAX_IDLE_DAYS = 30
        assert preview.preview_allowed(channel)

    def test_native_disabled_with_zero_days(self, settings):
        """``PUSH_PREVIEW_MAX_IDLE_DAYS=0`` turns native previews off."""
        settings.PUSH_PREVIEW_MAX_IDLE_DAYS = 0
        assert not preview.preview_allowed(
            _channel(factories.UserFactory(), keys=Device().keys)
        )

    def test_master_switch(self, settings):
        """``PUSH_PREVIEWS_ENABLED=False`` keeps every device content-free."""
        settings.PUSH_PREVIEWS_ENABLED = False
        assert not preview.preview_allowed(
            _channel(factories.UserFactory(), keys=Device().keys)
        )
        assert not preview.preview_allowed(
            self._web(timezone.now() + timedelta(hours=1))
        )


# ---------------------------------------------------------------------------
# The sealed preview
# ---------------------------------------------------------------------------


class TestWithPreview:
    """Building, sealing and padding the preview for one device."""

    def test_native_preview_decrypts_with_the_device_key(self):
        """Only the device private key opens the preview; the gateway sees the
        ciphertext and the id of the key it was sealed for."""
        user, message = _message(subject="Quarterly report", sender_name="Alice")
        device = Device()
        channel = _channel(user, keys=device.keys)

        payload = preview.with_preview(channel, device.keys, _thin(message))

        assert "Quarterly report" not in json.dumps(payload)
        assert "preview" not in payload
        assert payload["kid"] == preview.key_id(device.keys["p256dh"])
        plaintext = device.open(payload["enc"])
        # Padded to a fixed bucket so the length leaks nothing.
        assert len(plaintext) in preview.PAD_BUCKETS
        assert json.loads(plaintext) == {
            "sender": "Alice",
            "subject": "Quarterly report",
        }
        # The routing ids are kept as is.
        assert payload["message_id"] == str(message.id)

    def test_another_device_cannot_open_it(self):
        """A preview sealed for one device does not decrypt with another key."""
        user, message = _message()
        device = Device()
        payload = preview.with_preview(
            _channel(user, keys=device.keys), device.keys, _thin(message)
        )
        with pytest.raises(Exception):  # noqa: B017
            Device().open(payload["enc"])

    def test_web_preview_is_left_to_transport_encryption(self):
        """Web Push already encrypts the whole payload for the browser (RFC
        8291): the preview goes in clear inside it, the service worker can't
        reach the browser's private key to open a second layer."""
        user, message = _message(subject="Lunch?", sender_name="Bob")
        device = Device()
        channel = _channel(
            user,
            platform=PushPlatformChoices.WEB,
            keys=device.keys,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )
        payload = preview.with_preview(channel, device.keys, _thin(message))
        assert payload["preview"] == {"sender": "Bob", "subject": "Lunch?"}
        assert "enc" not in payload

    def test_sender_falls_back_to_email_and_fields_are_truncated(self):
        """Nameless senders show their address; long values are capped so the
        sealed preview always fits the gateways' payload limits."""
        _user, message = _message(
            subject="s" * 255, sender_name="", sender_email="bob@example.com"
        )
        assert preview.build_preview(message) == {
            "sender": "bob@example.com",
            "subject": "s" * preview.PREVIEW_SUBJECT_MAX_CHARS,
        }
        message.sender.name = "n" * 255
        assert len(preview.build_preview(message)["sender"]) == (
            preview.PREVIEW_SENDER_MAX_CHARS
        )

    def test_worst_case_preview_fits_apns_limit(self):
        """Every truncated char 4 bytes long still fits the largest bucket and
        the 4 KB APNs payload once sealed and encoded."""
        _user, message = _message(subject="😀" * 255, sender_name="😀" * 255)
        device = Device()
        plaintext = preview.pad_json(preview.build_preview(message))
        assert len(plaintext) == preview.PAD_BUCKETS[-1]
        enc = preview.b64url_encode(preview.encrypt_for_device(plaintext, device.keys))
        assert len(enc) < 3500  # leaves room for aps + routing ids under 4096

    def test_not_allowed_keeps_the_thin_payload(self, settings):
        """A device outside its preview window gets the generic banner."""
        settings.PUSH_PREVIEW_MAX_IDLE_DAYS = 0
        user, message = _message()
        device = Device()
        thin = _thin(message)
        assert (
            preview.with_preview(_channel(user, keys=device.keys), device.keys, thin)
            == thin
        )

    def test_without_keys_keeps_the_thin_payload(self):
        """An app that never registered keys gets the generic banner."""
        user, message = _message()
        thin = _thin(message)
        assert preview.with_preview(_channel(user), None, thin) == thin

    def test_failures_keep_the_thin_payload_and_log_no_content(self, caplog):
        """A message deleted since dispatch, or an unusable key, never breaks
        the push: it falls back to the generic banner, logging no content."""
        user, message = _message(subject="Top secret")
        device = Device()
        channel = _channel(user, keys=device.keys)
        thin = _thin(message)

        broken_keys = {"p256dh": preview.b64url_encode(b"x" * 65), "auth": "AAAA"}
        assert preview.with_preview(channel, broken_keys, thin) == thin

        message.delete()
        assert preview.with_preview(channel, device.keys, thin) == thin
        assert "Top secret" not in caplog.text

    def test_key_id_is_short_and_stable(self):
        """The key id identifies a key pair across rotations, compactly."""
        keys = Device().keys
        assert preview.key_id(keys["p256dh"]) == preview.key_id(keys["p256dh"])
        assert len(preview.key_id(keys["p256dh"])) == 11
        assert preview.key_id(keys["p256dh"]) != preview.key_id(Device().keys["p256dh"])

    def test_padding_buckets(self):
        """Payloads are padded up to the first bucket that fits, as valid JSON."""
        small = preview.pad_json({"a": "b"})
        assert len(small) == 512
        assert json.loads(small) == {"a": "b"}
        assert len(preview.pad_json({"a": "b" * 600})) == 1024


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


class TestDelivery:
    """The preview is added at send time and carried by each transport."""

    def test_dispatched_task_arguments_stay_content_free(self, settings):
        """Task arguments go through the broker and are persisted by the result
        backend: the orchestrator must only dispatch the thin payload."""
        settings.PUSH_ENABLED = True
        user, message = _message(subject="Confidential merger")
        _channel(user, keys=Device().keys)
        with mock.patch.object(push.send_push_notification, "delay") as delay:
            push.send_push_for_message(str(message.id))
        delay.assert_called_once()
        assert "Confidential merger" not in json.dumps(delay.call_args.args)
        assert "Alice" not in json.dumps(delay.call_args.args)

    def test_native_sender_receives_the_sealed_preview(self, settings):
        """The per-device task seals the preview right before sending."""
        settings.PUSH_ENABLED = True
        user, message = _message(subject="Confidential merger")
        device = Device()
        _channel(user, keys=device.keys)
        with mock.patch.object(
            push, "send_apns", return_value=push.PushResult(1, 0)
        ) as apns:
            push.send_push_for_message(str(message.id))
        ((_channel_sent, payload),) = apns.call_args.args[0]
        assert json.loads(device.open(payload["enc"]))["subject"] == (
            "Confidential merger"
        )

    def test_apns_carries_enc_and_expires(self, settings):
        """APNs keeps the generic alert as fallback, routes it through the
        Notification Service Extension, and drops it after the TTL."""
        settings.PUSH_ENABLED = True
        settings.PUSH_APNS_KEY = "k"
        settings.PUSH_APNS_KEY_ID = "KEYID"
        settings.PUSH_APNS_TEAM_ID = "TEAM"
        settings.PUSH_APNS_BUNDLE_ID = "com.example.app"
        channel = _channel(factories.UserFactory())
        client = mock.MagicMock()
        client.post.return_value = mock.Mock(status_code=200)
        with (
            mock.patch.object(push.apns, "_apns_auth_token", return_value="tok"),
            mock.patch.object(push.apns, "_apns_client", return_value=client),
        ):
            push.send_apns([(channel, {"enc": "sealed", "kid": "k1"})], "thread-x")

        body = client.post.call_args.kwargs["json"]
        assert body["enc"] == "sealed"
        assert body["kid"] == "k1"
        assert body["aps"]["mutable-content"] == 1
        assert body["aps"]["alert"] == {"loc-key": push.APNS_ALERT_LOC_KEY}
        # Contract with the category the iOS app registers (AppDelegate,
        # pinned by push-preview-invariants.test.ts): its placeholder replaces
        # the content when the user's "Show Previews" setting hides it.
        assert body["aps"]["category"] == push.APNS_CATEGORY == "NEW_MESSAGE"
        expiration = int(client.post.call_args.kwargs["headers"]["apns-expiration"])
        expected = timezone.now().timestamp() + push.PUSH_TTL_SECONDS
        assert abs(expiration - expected) < 5

    @pytest.mark.parametrize(
        ("payload", "has_notification"),
        [
            ({"type": "new_message", "enc": "sealed", "kid": "k1"}, False),
            ({"type": "new_message"}, True),
        ],
    )
    def test_fcm_goes_data_only_with_a_sealed_preview(
        self, settings, payload, has_notification
    ):
        """Android only hands a background message to the app (which decrypts
        it) when it has no notification block; without a preview the generic
        notification block stays, so force-quit apps still get a banner."""
        settings.PUSH_ENABLED = True
        settings.PUSH_FCM_CREDENTIALS = '{"type": "service_account"}'
        settings.PUSH_FCM_PROJECT_ID = "my-project"
        channel = _channel(factories.UserFactory(), platform=PushPlatformChoices.FCM)
        client = mock.MagicMock()
        client.post.return_value = mock.Mock(status_code=200)
        with (
            mock.patch.object(push.fcm, "_fcm_access_token", return_value="ya29"),
            mock.patch.object(push.fcm, "_fcm_client", return_value=client),
        ):
            push.send_fcm([(channel, payload)], "thread-x")

        message = client.post.call_args.kwargs["json"]["message"]
        assert ("notification" in message["android"]) is has_notification
        assert message["android"]["ttl"] == f"{push.PUSH_TTL_SECONDS}s"
        assert message["android"]["priority"] == "high"
        if not has_notification:
            assert message["data"]["enc"] == "sealed"
            assert message["data"]["kid"] == "k1"

    def test_webpush_pads_the_payload(self, settings):
        """The browser-encrypted payload is padded too: its preview length must
        not show through the ciphertext the push service relays."""
        settings.PUSH_ENABLED = True
        settings.PUSH_VAPID_PRIVATE_KEY = "vapid-key"
        settings.PUSH_VAPID_SUBJECT = "mailto:ops@example.com"
        channel = _channel(
            factories.UserFactory(),
            platform=PushPlatformChoices.WEB,
            keys=Device().keys,
        )
        session = mock.Mock()
        session.post.return_value = mock.Mock(status_code=201)
        vapid = mock.Mock()
        vapid.sign.return_value = {"Authorization": "vapid t=x,k=y"}
        payload = {"type": "new_message", "preview": {"sender": "A", "subject": "B"}}
        with (
            mock.patch.object(push.webpush, "_load_vapid", return_value=vapid),
            mock.patch("http_ece.encrypt", return_value=b"ciphertext") as encrypt,
            mock.patch.object(push.webpush, "SSRFSafeSession", return_value=session),
        ):
            push.send_webpush([(channel, payload)], "thread-x")

        plaintext = encrypt.call_args.args[0]
        assert len(plaintext) in preview.PAD_BUCKETS
        assert json.loads(plaintext) == payload
