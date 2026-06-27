"""Tests for the mobile/web push-notification workstream.

Push targets are modelled as user-scoped ``Channel`` rows of type ``push``
(one per device) — so the device token lives encrypted in
``encrypted_settings`` and users get device management (list / un-associate)
for free via ``/users/me/channels/``.

Covers: device registration (incl. reclaim on account switch), the thin
privacy-preserving payload, the ``enqueue_push_notifications`` on-commit
helper, the ``send_push_for_message`` task, each sender's happy path + its
stale-device removal, and the ``PUSH_ENABLED`` master switch.

Every external gateway (APNs / FCM / Web Push) is mocked — no network.
"""
# pylint: disable=too-many-lines, protected-access, no-value-for-parameter

import base64
import hashlib
import logging
from io import StringIO
from unittest import mock

from django.contrib.auth import logout as auth_logout
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models
from core.enums import ChannelScopeLevel, ChannelTypes, PushPlatformChoices
from core.services import push
from core.services.push import common as push_common
from core.services.push.common import session_hash

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_with_message():
    """A user with access to a mailbox+thread, and a message in it."""
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=enums.MailboxRoleChoices.EDITOR
    )
    thread = factories.ThreadFactory(messaged_at=timezone.now())
    factories.ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    message = factories.MessageFactory(thread=thread)
    return user, message


def _push_channel(user, platform=PushPlatformChoices.APNS, token="tok", keys=None):
    """Create a push channel for ``user`` via the real registration helper."""
    channel, _ = push.register_push_device(
        user=user, platform=platform, token=token, keys=keys
    )
    return channel


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


# Realistic base64url subscription keys: a 65-byte uncompressed P-256 point and
# a 16-byte auth secret. The sender base64url-decodes them before encrypting
# (http_ece is mocked, so the exact bytes are inert on the send path).
_WEB_KEYS = {"p256dh": _b64url(b"x" * 65), "auth": _b64url(b"y" * 16)}


# ---------------------------------------------------------------------------
# Device registration (as user-scoped push channels)
# ---------------------------------------------------------------------------


class TestDeviceRegistration:
    """Device registration (POST type=push) + management via /users/me/channels/."""

    @pytest.fixture(autouse=True)
    def _enable_push(self, settings):
        # Registration refuses platforms whose gateway is unconfigured, so
        # give every transport credentials by default.
        _configure_apns(settings)
        _configure_fcm(settings)
        _configure_webpush(settings)

    def _register(self, client, **body):
        # Push devices register through the generic user-channels create with
        # type=push (idempotent upsert), not a dedicated endpoint.
        return client.post(
            reverse("user-channels-list"),
            {"type": "push", **body},
            format="json",
        )

    def test_requires_authentication(self):
        """Registration is rejected for anonymous callers."""
        resp = self._register(APIClient(), platform="apns", token="abc")
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    @pytest.mark.parametrize(
        ("platform", "missing_setting", "extra_body"),
        [
            ("apns", "PUSH_APNS_KEY", {}),
            ("fcm", "PUSH_FCM_CREDENTIALS", {}),
            ("web", "PUSH_VAPID_PRIVATE_KEY", {"keys": _WEB_KEYS}),
        ],
    )
    def test_register_refuses_unconfigured_platform(
        self, settings, platform, missing_setting, extra_body
    ):
        """A platform whose gateway lacks credentials is refused with an
        explicit 400: accepting the device would enroll it into a black hole
        (its sender no-ops), and this error is the client's only signal that
        the deployment doesn't serve its transport."""
        setattr(settings, missing_setting, None)
        client = APIClient()
        client.force_authenticate(user=factories.UserFactory())

        resp = self._register(client, platform=platform, token="tok-1", **extra_body)

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "platform" in resp.json()
        assert not models.Channel.objects.filter(type=ChannelTypes.PUSH).exists()

    def test_register_creates_push_channel(self):
        """A first registration creates a user-scoped push channel with the
        token stored encrypted (never in queryable settings) and hashed for
        dedup."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)

        resp = self._register(
            client, platform="fcm", token="fcm-1", app_version="2.3.0"
        )
        assert resp.status_code == status.HTTP_201_CREATED

        ch = models.Channel.objects.get(type=ChannelTypes.PUSH, user=user)
        assert ch.scope_level == ChannelScopeLevel.USER
        assert ch.settings["platform"] == "fcm"
        assert ch.settings["app_version"] == "2.3.0"
        # Token is stored ENCRYPTED, not in queryable settings.
        assert ch.encrypted_settings["token"] == "fcm-1"
        assert "token" not in ch.settings
        # Dedup key is the indexed column, not buried in settings; the token is
        # namespaced with a "push:" prefix before hashing (see _token_hash).
        assert ch.lookup_hash == hashlib.sha256(b"push:fcm-1").hexdigest()
        assert "token_hash" not in ch.settings

    def test_token_hash_contract_vector(self):
        """Pin _token_hash to a shared vector the frontend also asserts.

        The web client recomputes this exact value (``hashEndpoint`` in
        web-push.ts, asserted against the same literal in web-push.test.ts) to
        recognise its own device row by ``token_hash``. The two implementations
        must stay byte-identical, so both sides lock onto one known vector — any
        drift on either side breaks device sign-out / shared-computer takeover.
        """
        assert (
            push_common._token_hash("https://push.example/ep-123")
            == "aa90f805f294edd82e4284a23521c8b0067582a63c70fb030ddc77214bf8cf7b"
        )

    def test_reregister_same_device_updates_in_place(self):
        """Re-registering the same token refreshes the existing row (200) rather
        than creating a duplicate."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)

        assert (
            self._register(
                client, platform="apns", token="apns-1", app_version="1.0.0"
            ).status_code
            == status.HTTP_201_CREATED
        )
        resp = self._register(
            client, platform="apns", token="apns-1", app_version="9.9.9"
        )
        assert resp.status_code == status.HTTP_200_OK  # refresh, not create
        assert (
            models.Channel.objects.filter(type=ChannelTypes.PUSH, user=user).count()
            == 1
        )
        ch = models.Channel.objects.get(type=ChannelTypes.PUSH, user=user)
        assert ch.settings["app_version"] == "9.9.9"

    def test_register_reclaims_device_from_other_user(self):
        """A token re-registered by a new user (device changed accounts) is a
        delete + fresh create: the old row is gone, and the new one does NOT
        inherit the previous owner's id or device label."""
        old = factories.UserFactory()
        old_channel, _ = push.register_push_device(
            user=old, platform="apns", token="shared-device", name="Old User iPhone"
        )

        new = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=new)
        # New owner sends no name → must fall back to the platform default,
        # never the previous owner's label.
        resp = self._register(client, platform="apns", token="shared-device")
        assert resp.status_code == status.HTTP_201_CREATED  # fresh row, not refresh

        assert not models.Channel.objects.filter(
            user=old, type=ChannelTypes.PUSH
        ).exists()
        new_channels = models.Channel.objects.filter(user=new, type=ChannelTypes.PUSH)
        assert new_channels.count() == 1
        new_channel = new_channels.get()
        assert new_channel.id != old_channel.id  # fresh id, no cross-user reuse
        assert new_channel.name != "Old User iPhone"  # no label leak

    def test_register_reclaims_under_concurrent_foreign_insert(self):
        """A foreign row for the same token that materialises *between* our delete
        and our create (concurrent first-time registration on a shared device)
        still resolves to a delete + fresh create — never a silent in-place update
        that would carry the other user's id or device label to the caller."""
        other = factories.UserFactory()
        push.register_push_device(
            user=other, platform="apns", token="shared", name="Other iPhone"
        )
        foreign_id = models.Channel.objects.get(user=other).id
        caller = factories.UserFactory()

        # Model the race: the first reclaim's delete runs before the foreign row
        # commits (so it finds nothing → no-op), the retry's delete sees it.
        real_drop = push_common._drop_other_users_token
        calls = []

        def flaky_drop(user, token_hash):
            calls.append(1)
            if len(calls) == 1:
                return  # our delete found nothing; the foreign row is still there
            real_drop(user, token_hash)

        with mock.patch.object(
            push_common, "_drop_other_users_token", side_effect=flaky_drop
        ):
            channel, created = push.register_push_device(
                user=caller, platform="apns", token="shared"
            )

        assert len(calls) == 2  # first upsert conflicted → reclaim was retried
        assert created  # caller got a brand-new row, not an in-place update
        assert channel.user_id == caller.id
        assert channel.id != foreign_id  # no id inheritance
        assert channel.name != "Other iPhone"  # no label leak
        assert not models.Channel.objects.filter(user=other).exists()
        assert models.Channel.objects.filter(type=ChannelTypes.PUSH).count() == 1

    def test_registration_prunes_beyond_device_cap(self, settings):
        """A new device beyond PUSH_MAX_DEVICES_PER_USER evicts the LRU one."""
        settings.PUSH_MAX_DEVICES_PER_USER = 2
        user = factories.UserFactory()
        for i in range(4):
            push.register_push_device(user=user, platform="apns", token=f"d{i}")
        assert (
            models.Channel.objects.filter(type=ChannelTypes.PUSH, user=user).count()
            == 2
        )
        # The most recently registered device is always kept.
        assert models.Channel.objects.filter(
            lookup_hash=hashlib.sha256(b"push:d3").hexdigest()
        ).exists()

    def test_empty_token_rejected(self):
        """A blank/whitespace-only token is rejected as a bad request."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)
        resp = self._register(client, platform="apns", token="   ")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_404_when_push_disabled(self, settings):
        """The endpoint is hidden (not just inert) while the feature is off."""
        settings.PUSH_ENABLED = False
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)
        resp = self._register(client, platform="apns", token="apns-1")
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert not models.Channel.objects.filter(type=ChannelTypes.PUSH).exists()

    def test_web_platform_requires_keys(self):
        """Web Push registration requires the full p256dh+auth key pair; missing
        or partial keys are rejected."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)
        # No keys at all → rejected.
        resp = self._register(client, platform="web", token="endpoint-url")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        # Partial keys → still rejected.
        resp = self._register(
            client,
            platform="web",
            token="endpoint-url",
            keys={"p256dh": _WEB_KEYS["p256dh"]},
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert not models.Channel.objects.filter(type=ChannelTypes.PUSH).exists()

    def test_web_platform_rejects_non_string_keys(self):
        """The poison shape a bare DictField allowed — key *values* that aren't
        strings (``{p256dh: {...}, auth: [...]}``) — is rejected at
        registration. Persisted, it used to blow up deterministically at every
        send (retrying forever); the typed nested serializer stops it here.
        (Malformed-but-string keys are accepted and handled gracefully at send
        time — marked stale, never retried — so we don't over-validate them.)"""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)
        resp = self._register(
            client,
            platform="web",
            token="endpoint-url",
            keys={"p256dh": {"nested": 1}, "auth": [1, 2]},
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert not models.Channel.objects.filter(type=ChannelTypes.PUSH).exists()

    def test_web_platform_with_keys_creates_channel(self):
        """A Web Push registration with both valid keys creates the channel."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)
        resp = self._register(
            client,
            platform="web",
            token="endpoint-url",
            keys=_WEB_KEYS,
        )
        assert resp.status_code == status.HTTP_201_CREATED
        ch = models.Channel.objects.get(type=ChannelTypes.PUSH, user=user)
        assert ch.settings["platform"] == "web"

    def test_push_channel_settings_not_patchable(self):
        """The generic channel update path must not let a client desync the
        queryable push metadata (platform) from the encrypted token — even
        when a rename rides along."""
        user = factories.UserFactory()
        ch = _push_channel(user, token="immutable")
        client = APIClient()
        client.force_authenticate(user=user)

        for body in (
            {"settings": {"platform": "fcm"}},
            {"name": "sneaky", "settings": {"platform": "fcm"}},
        ):
            resp = client.patch(
                reverse("user-channels-detail", kwargs={"pk": ch.id}),
                body,
                format="json",
            )
            assert resp.status_code == status.HTTP_400_BAD_REQUEST

        ch.refresh_from_db()
        assert ch.settings["platform"] == "apns"
        assert ch.name != "sneaky"

    def test_push_channel_rename_via_patch(self):
        """Renaming is the one generic edit allowed on a push channel: `name`
        is display-only metadata, and re-registration can't rename a *remote*
        device."""
        user = factories.UserFactory()
        ch = _push_channel(user, token="renamable")
        client = APIClient()
        client.force_authenticate(user=user)

        resp = client.patch(
            reverse("user-channels-detail", kwargs={"pk": ch.id}),
            {"name": "Mon iPhone pro"},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        ch.refresh_from_db()
        assert ch.name == "Mon iPhone pro"
        # The sync-sensitive columns are untouched.
        assert ch.settings["platform"] == "apns"
        assert ch.lookup_hash == hashlib.sha256(b"push:renamable").hexdigest()

    def test_list_shows_devices_without_leaking_token(self):
        """Listing a user's channels never serializes the encrypted token."""
        user = factories.UserFactory()
        _push_channel(user, token="secret-token")
        client = APIClient()
        client.force_authenticate(user=user)

        resp = client.get(reverse("user-channels-list"))
        assert resp.status_code == status.HTTP_200_OK
        blob = str(resp.json())
        assert "secret-token" not in blob  # encrypted_settings never serialized

    def test_unregister_via_channel_delete(self):
        """Deleting the channel un-associates the device."""
        user = factories.UserFactory()
        ch = _push_channel(user, token="to-remove")
        client = APIClient()
        client.force_authenticate(user=user)

        resp = client.delete(reverse("user-channels-detail", kwargs={"pk": ch.id}))
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not models.Channel.objects.filter(id=ch.id).exists()

    def test_non_dict_body_is_bad_request_not_server_error(self):
        """A top-level JSON body that is not an object (e.g. a list) must be a
        400, not a 500. The push-registration sniffing does ``data.get("type")``
        in both ``get_throttles`` (inside ``check_throttles``, before the
        handler) and ``create``; on a list that ``.get`` would raise
        ``AttributeError`` and escape as a 500. The guard treats a non-dict body
        as not-push so it falls through to the serializer's 400."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)

        resp = client.post(reverse("user-channels-list"), [], format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert not models.Channel.objects.filter(type=ChannelTypes.PUSH).exists()


# ---------------------------------------------------------------------------
# Voluntary logout unregisters the session's device
# ---------------------------------------------------------------------------


class TestLogoutUnregistersDevice:
    """Voluntary logout must stop this device's notifications, server-side.

    The channel is stamped with a hash of the registering session; the
    ``user_logged_out`` receiver deletes the channels matching the session
    being destroyed. An *expired* session reaches the logout view anonymous,
    so nothing is deleted — notifications survive expiry by design.
    """

    @pytest.fixture(autouse=True)
    def _enable_push(self, settings):
        # The API registrations below use platform=web, which registration
        # only accepts when the Web Push gateway is configured.
        _configure_webpush(settings)

    @staticmethod
    def _request_with_session(user):
        """A request carrying a real (saved) session, as auth.logout needs."""
        request = RequestFactory().post("/logout/")
        SessionMiddleware(lambda r: r).process_request(request)
        request.session.create()
        request.user = user
        return request

    def test_api_registration_stamps_session_hash(self):
        """Registering over a session-authenticated request stamps the channel
        with the session hash — and never the raw session key."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_login(user)
        session_key = client.session.session_key

        resp = client.post(
            reverse("user-channels-list"),
            {
                "type": "push",
                "platform": "web",
                "token": "endpoint-1",
                "keys": _WEB_KEYS,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED

        ch = models.Channel.objects.get(type=ChannelTypes.PUSH, user=user)
        assert ch.settings["session_hash"] == session_hash(session_key)
        assert session_key not in str(ch.settings)

    def test_registration_without_session_has_no_stamp(self):
        """A sessionless registration (e.g. token-authenticated) is simply not
        logout-bound: no stamp, and logout never matches it."""
        user = factories.UserFactory()
        channel = _push_channel(user, token="no-session")
        assert "session_hash" not in channel.settings

    def test_logout_deletes_only_this_sessions_channel(self):
        """auth.logout removes the channel registered under the logging-out
        session; the same user's other devices keep receiving."""
        user = factories.UserFactory()
        request = self._request_with_session(user)
        this_device, _ = push.register_push_device(
            user=user,
            platform=PushPlatformChoices.WEB,
            token="this-browser",
            keys=_WEB_KEYS,
            session_key=request.session.session_key,
        )
        other_device, _ = push.register_push_device(
            user=user,
            platform=PushPlatformChoices.WEB,
            token="other-browser",
            keys=_WEB_KEYS,
            session_key="another-live-session",
        )

        auth_logout(request)

        assert not models.Channel.objects.filter(id=this_device.id).exists()
        assert models.Channel.objects.filter(id=other_device.id).exists()

    def test_anonymous_logout_is_noop(self):
        """The 401/session-expiry funnel hits the logout view anonymous: no
        channel may be deleted, so notifications survive expiry."""
        user = factories.UserFactory()
        request = self._request_with_session(AnonymousUser())
        channel, _ = push.register_push_device(
            user=user,
            platform=PushPlatformChoices.WEB,
            token="survives-expiry",
            keys=_WEB_KEYS,
            session_key=request.session.session_key,
        )

        auth_logout(request)

        assert models.Channel.objects.filter(id=channel.id).exists()


# ---------------------------------------------------------------------------
# Thin payload & collapse key
# ---------------------------------------------------------------------------


class TestThinPayload:
    """The thin, content-free payload and its per-thread collapse/topic keys."""

    def test_payload_is_thin_and_content_free(self):
        """The payload carries only routing ids + unread count — never subject
        or body."""
        _, message = _user_with_message()
        message.subject = "Secret subject that must not leak"

        payload = push.build_thin_payload(message, unread_count=7, mailbox_id="mb-123")
        assert payload == {
            "type": push.PUSH_TYPE_NEW_MESSAGE,
            "thread_id": str(message.thread_id),
            "message_id": str(message.id),
            "mailbox_id": "mb-123",
            "unread_count": 7,
        }
        # Routing ids only — no message content of any kind.
        assert "Secret subject" not in str(payload)
        assert "subject" not in payload and "body" not in payload

    def test_collapse_key_is_per_thread(self):
        """The collapse key is derived per thread so re-sends coalesce."""
        _, message = _user_with_message()
        assert push.collapse_key_for_message(message) == f"thread-{message.thread_id}"

    def test_web_push_topic_within_rfc_limit(self):
        """RFC 8030 caps the Web Push Topic at 32 url-safe-base64 chars; the raw
        thread-<uuid> collapse key is 43, so it must be hashed down."""
        _, message = _user_with_message()
        raw = push.collapse_key_for_message(message)
        assert len(raw) > 32  # the raw key would be rejected as-is
        topic = push.webpush._web_push_topic(raw)
        assert 0 < len(topic) <= 32
        assert all(c.isalnum() or c in "-_" for c in topic)
        # Stable for a given key (coalescing must keep working).
        assert topic == push.webpush._web_push_topic(raw)


# ---------------------------------------------------------------------------
# enqueue helper
# ---------------------------------------------------------------------------


class TestEnqueue:
    """The ``enqueue_push_notifications`` on-commit helper and its master switch."""

    def test_noop_when_disabled(self, settings):
        """No task is enqueued while the feature is off."""
        settings.PUSH_ENABLED = False
        _, message = _user_with_message()
        with mock.patch.object(push.send_push_for_message, "delay") as delay:
            push.enqueue_push_notifications(message)
        delay.assert_not_called()

    def test_enqueues_on_commit_when_enabled(
        self, settings, django_capture_on_commit_callbacks
    ):
        """The task is dispatched once, on transaction commit."""
        settings.PUSH_ENABLED = True
        _, message = _user_with_message()
        with mock.patch.object(push.send_push_for_message, "delay") as delay:
            with django_capture_on_commit_callbacks(execute=True):
                push.enqueue_push_notifications(message)
        delay.assert_called_once_with(str(message.id))

    def test_broker_failure_at_commit_is_swallowed(
        self, settings, django_capture_on_commit_callbacks
    ):
        """A broker outage must not escape the on-commit callback.

        The callback runs at commit time on the delivery pipeline's stack, which
        has already deleted its queue row by then: an escaping exception would
        make it retry a row that no longer exists.
        """
        settings.PUSH_ENABLED = True
        _, message = _user_with_message()
        with mock.patch.object(
            push.send_push_for_message, "delay", side_effect=OSError("broker down")
        ):
            with django_capture_on_commit_callbacks(execute=True):
                push.enqueue_push_notifications(message)


# ---------------------------------------------------------------------------
# send_push_for_message task
# ---------------------------------------------------------------------------


class TestSendPushTask:
    """The ``send_push_for_message`` fan-out task (one sub-task per device)."""

    def test_skips_when_disabled(self, settings):
        """The task short-circuits when the feature is off."""
        settings.PUSH_ENABLED = False
        _, message = _user_with_message()
        result = push.send_push_for_message(str(message.id))
        assert result["skipped"] == "push_disabled"

    def test_dispatches_one_task_per_device(self, settings):
        """Each of the recipient's devices gets its own per-device sub-task."""
        settings.PUSH_ENABLED = True
        user, message = _user_with_message()
        _push_channel(user, platform=PushPlatformChoices.APNS, token="a")
        _push_channel(user, platform=PushPlatformChoices.FCM, token="b")

        with (
            mock.patch.object(
                push, "send_apns", return_value=push.PushResult(1, 0)
            ) as apns,
            mock.patch.object(
                push, "send_fcm", return_value=push.PushResult(1, 0)
            ) as fcm,
        ):
            # Celery is eager in tests, so the dispatched per-device tasks run
            # inline and each call the (mocked) sender for its platform.
            result = push.send_push_for_message(str(message.id))

        assert result["dispatched"] == 2  # one task per device
        apns.assert_called_once()
        fcm.assert_called_once()
        # Each task hands its sender a single (channel, payload) pair.
        items, _collapse = apns.call_args.args
        assert len(items) == 1
        channel, payload = items[0]
        assert channel.type == ChannelTypes.PUSH
        assert payload["type"] == push.PUSH_TYPE_NEW_MESSAGE

    def test_dispatch_failure_on_one_device_spares_the_others(self, settings):
        """A broker failure on one device must not strand the rest of the devices.

        This task holds the only resolved recipient list, so a device skipped
        here is a push lost with no way to recover it.
        """
        settings.PUSH_ENABLED = True
        user, message = _user_with_message()
        _push_channel(user, platform=PushPlatformChoices.APNS, token="a")
        _push_channel(user, platform=PushPlatformChoices.FCM, token="b")

        with mock.patch.object(
            push.send_push_notification,
            "delay",
            side_effect=[OSError("broker down"), None],
        ) as delay:
            result = push.send_push_for_message(str(message.id))

        assert result["success"] is True
        assert delay.call_count == 2  # the second device is still attempted
        assert result["dispatched"] == 1  # only the device that got through counts

    def test_sender_exception_is_swallowed(self, settings):
        """A sender crash never propagates out of the fan-out task."""
        settings.PUSH_ENABLED = True
        user, message = _user_with_message()
        _push_channel(user, token="a")
        with mock.patch.object(push, "send_apns", side_effect=RuntimeError("boom")):
            result = push.send_push_for_message(str(message.id))
        assert result["success"] is True


# ---------------------------------------------------------------------------
# send_push_notification task (one per device, retryable on transient failures)
# ---------------------------------------------------------------------------


class TestSendPushNotification:
    """The per-device ``send_push_notification`` task and its retry semantics."""

    def test_skips_when_disabled(self, settings):
        """The per-device task short-circuits when the feature is off."""
        settings.PUSH_ENABLED = False
        out = push.send_push_notification("any-id", {"type": "new_message"}, "k")
        assert out["skipped"] == "push_disabled"

    def test_missing_channel_is_skipped(self, settings):
        """A channel deleted between dispatch and run is a no-op, not an error."""
        settings.PUSH_ENABLED = True
        out = push.send_push_notification(
            "00000000-0000-0000-0000-000000000000", {"type": "new_message"}, "k"
        )
        assert out["skipped"] == "channel_gone"

    def test_transient_failure_triggers_retry(self, settings):
        """A transient sender result makes the task retry just this device; the
        collapse key makes the re-send idempotent. Succeeds on the 2nd attempt."""
        settings.PUSH_ENABLED = True
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.FCM, token="b"
        )
        calls = []

        def sender(_items, _collapse):
            calls.append(1)
            return push.PushResult(0, 1) if len(calls) == 1 else push.PushResult(1, 0)

        with (
            mock.patch.object(push, "send_fcm", side_effect=sender),
            mock.patch("time.sleep"),  # neutralize any eager retry backoff
        ):
            result = push.send_push_notification.apply(
                args=(str(ch.id), {"type": "new_message"}, "k")
            )

        assert len(calls) == 2  # retried once after the transient failure
        assert result.result["success"] is True

    def test_sender_bug_does_not_loop_forever(self, settings):
        """A sender that raises (a bug, not a transient signal) ends the task
        rather than retrying — autoretry only fires on PushTransientError."""
        settings.PUSH_ENABLED = True
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.FCM, token="b"
        )
        with mock.patch.object(push, "send_fcm", side_effect=RuntimeError("boom")):
            out = push.send_push_notification(str(ch.id), {"type": "new_message"}, "k")
        assert out["error"] == "sender_raised"


# ---------------------------------------------------------------------------
# APNs sender  (httpx HTTP/2 mocked; _apns_auth_token mocked — no signing key)
# ---------------------------------------------------------------------------


@pytest.fixture(name="push_caplog")
def fixture_push_caplog(caplog):
    """caplog wired straight onto the push ``common`` logger.

    Its records don't reach the root handler pytest's ``caplog`` listens on
    (same workaround as the coalescer tests in ``test_signals.py``), so the
    handler is attached to the logger directly.
    """
    push_logger = logging.getLogger("core.services.push.common")
    caplog.set_level(logging.WARNING, logger="core.services.push.common")
    push_logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        push_logger.removeHandler(caplog.handler)


def _configure_apns(settings):
    settings.PUSH_ENABLED = True
    settings.PUSH_APNS_KEY = "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----"
    settings.PUSH_APNS_KEY_ID = "KEYID123"
    settings.PUSH_APNS_TEAM_ID = "TEAM123"
    settings.PUSH_APNS_BUNDLE_ID = "com.example.app"
    settings.PUSH_APNS_USE_SANDBOX = False


def _client_returning(response):
    """A mock HTTP client (the process-global one senders reuse) whose .post
    returns ``response``."""
    client = mock.MagicMock()
    client.post.return_value = response
    return client


def _items(*channels, payload=None):
    """Build the (channel, payload) list senders now take."""
    payload = payload if payload is not None else {"type": "new_message"}
    return [(ch, payload) for ch in channels]


class TestApnsSender:
    """The APNs sender: delivery, stale-device removal, and transient handling."""

    def test_noop_when_not_configured(self, settings, push_caplog):
        """Without an APNs signing key the sender delivers nothing — and warns
        once per process (not per notification) instead of dropping silently."""
        settings.PUSH_ENABLED = True
        settings.PUSH_APNS_KEY = None
        push.common.warn_gateway_unconfigured.cache_clear()
        ch = _push_channel(factories.UserFactory(), platform=PushPlatformChoices.APNS)
        assert push.send_apns(_items(ch), "thread-x").delivered == 0
        assert push.send_apns(_items(ch), "thread-x").delivered == 0
        warnings = [
            r for r in push_caplog.records if "gateway not configured" in r.getMessage()
        ]
        assert len(warnings) == 1
        assert "'apns'" in warnings[0].getMessage()

    def test_success(self, settings):
        """A 200 from APNs counts as delivered and keeps the device."""
        _configure_apns(settings)
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.APNS, token="apns-ok"
        )
        cm = _client_returning(mock.Mock(status_code=200))
        with (
            mock.patch.object(push.apns, "_apns_auth_token", return_value="tok"),
            mock.patch.object(push.apns, "_apns_client", return_value=cm),
        ):
            result = push.send_apns(_items(ch), "thread-x")

        assert result.delivered == 1
        assert models.Channel.objects.filter(id=ch.id).exists()
        post = cm.post
        post.assert_called_once()
        assert post.call_args.kwargs["json"]["aps"]["alert"] == {
            "loc-key": push.APNS_ALERT_LOC_KEY
        }

    def test_unregistered_410_removes_channel(self, settings):
        """A 410 Unregistered is a dead token: the channel is deleted."""
        _configure_apns(settings)
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.APNS, token="dead"
        )
        resp = mock.Mock(status_code=410)
        resp.json.return_value = {"reason": "Unregistered"}
        with (
            mock.patch.object(push.apns, "_apns_auth_token", return_value="tok"),
            mock.patch.object(
                push.apns, "_apns_client", return_value=_client_returning(resp)
            ),
        ):
            push.send_apns(_items(ch), "thread-x")

        assert not models.Channel.objects.filter(id=ch.id).exists()

    def test_config_error_keeps_channel(self, settings):
        """A topic/config rejection is not a dead token: the channel is kept."""
        _configure_apns(settings)
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.APNS, token="cfg"
        )
        resp = mock.Mock(status_code=400)
        resp.json.return_value = {"reason": "DeviceTokenNotForTopic"}
        with (
            mock.patch.object(push.apns, "_apns_auth_token", return_value="tok"),
            mock.patch.object(
                push.apns, "_apns_client", return_value=_client_returning(resp)
            ),
        ):
            push.send_apns(_items(ch), "thread-x")

        assert models.Channel.objects.filter(id=ch.id).exists()

    def test_bad_device_token_keeps_channel(self, settings):
        """``BadDeviceToken`` is NOT treated as a dead device: it's most often a
        wrong-environment (sandbox/prod) config error, so deleting on it would
        purge live devices on a mis-set flag. The row is kept (logged only)."""
        _configure_apns(settings)
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.APNS, token="badenv"
        )
        resp = mock.Mock(status_code=400)
        resp.json.return_value = {"reason": "BadDeviceToken"}
        with (
            mock.patch.object(push.apns, "_apns_auth_token", return_value="tok"),
            mock.patch.object(
                push.apns, "_apns_client", return_value=_client_returning(resp)
            ),
        ):
            result = push.send_apns(_items(ch), "thread-x")

        assert models.Channel.objects.filter(id=ch.id).exists()
        assert result.transient == 0  # permanent rejection, not retried

    def test_transient_5xx_is_counted_and_keeps_channel(self, settings):
        """A 5xx is transient: keep the device and count it so the batch retries."""
        _configure_apns(settings)
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.APNS, token="t5xx"
        )
        resp = mock.Mock(status_code=503)
        resp.json.return_value = {}
        with (
            mock.patch.object(push.apns, "_apns_auth_token", return_value="tok"),
            mock.patch.object(
                push.apns, "_apns_client", return_value=_client_returning(resp)
            ),
        ):
            result = push.send_apns(_items(ch), "thread-x")

        assert result.delivered == 0
        assert result.transient == 1
        assert models.Channel.objects.filter(id=ch.id).exists()

    def test_provider_token_is_cached_across_calls(self, settings):
        """The ES256 provider token is minted once and reused (Apple throttles
        re-minting), so a second call within the TTL is served from cache."""
        cache.clear()
        _configure_apns(settings)
        with mock.patch("jwt.encode", return_value="signed-jwt") as enc:
            first = push.apns._apns_auth_token()
            second = push.apns._apns_auth_token()

        assert first == second == "signed-jwt"
        enc.assert_called_once()

    def test_unusable_signing_key_is_permanent_not_transient(self, settings):
        """A malformed signing key must not be retried.

        Nothing about waiting makes a bad PEM parse, so counting it transient
        would burn the whole retry budget (5 attempts backing off to 10min) on
        every device, for every message, until PUSH_APNS_KEY is fixed.
        """
        cache.clear()  # a cached token would bypass the minting path entirely
        _configure_apns(settings)  # whose key is a deliberately malformed PEM
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.APNS, token="apns-ok"
        )

        # No _apns_auth_token mock here, unlike its neighbours: the real minting
        # path is what's under test.
        result = push.send_apns(_items(ch), "thread-x")

        assert result == push.PushResult(0, 0)  # nothing sent, nothing retried
        assert models.Channel.objects.filter(id=ch.id).exists()  # a config fault
        # is not the device's fault

    def test_wrong_signing_key_type_is_permanent(self, settings):
        """A well-formed key of the wrong type is permanent too.

        PyJWT raises InvalidKeyError (not the ValueError a malformed PEM gets)
        when the key parses but isn't EC — e.g. an RSA key pasted where ES256
        needs P-256.
        """
        cache.clear()
        _configure_apns(settings)
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.APNS, token="apns-ok"
        )
        with mock.patch(
            "jwt.encode", side_effect=jwt.exceptions.InvalidKeyError("not an EC key")
        ):
            result = push.send_apns(_items(ch), "thread-x")

        assert result == push.PushResult(0, 0)
        assert models.Channel.objects.filter(id=ch.id).exists()

    def test_unexpected_mint_failure_stays_transient(self, settings):
        """Only the key itself is permanent: a mint failure from anything else
        (the cache backend down, Apple throttling) is still worth retrying."""
        _configure_apns(settings)
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.APNS, token="apns-ok"
        )
        with mock.patch.object(
            push.apns, "_apns_auth_token", side_effect=RuntimeError("cache down")
        ):
            result = push.send_apns(_items(ch), "thread-x")

        assert result.transient == 1
        assert result.delivered == 0

    def test_alert_is_visible_high_priority_and_content_free(self, settings):
        """The APNs push is a visible, high-priority alert that survives app
        termination — but still carries only a localization key + badge, never
        the message content."""
        _configure_apns(settings)
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.APNS, token="alert"
        )
        cm = _client_returning(mock.Mock(status_code=200))
        with (
            mock.patch.object(push.apns, "_apns_auth_token", return_value="tok"),
            mock.patch.object(push.apns, "_apns_client", return_value=cm),
        ):
            push.send_apns(
                _items(ch, payload={"type": "new_message", "unread_count": 5}),
                "thread-x",
            )

        post = cm.post
        headers = post.call_args.kwargs["headers"]
        assert headers["apns-push-type"] == "alert"
        assert headers["apns-priority"] == "10"
        aps = post.call_args.kwargs["json"]["aps"]
        assert aps["alert"] == {"loc-key": push.APNS_ALERT_LOC_KEY}
        assert aps["badge"] == 5
        assert "content-available" not in aps

    def test_unreadable_settings_are_skipped_not_transient(self, settings):
        """A device whose ``encrypted_settings`` no longer decrypts (a Fernet
        key rotation leaves ``to_python`` returning the raw JSON string) is a
        PERMANENT failure, uniform across senders: skip it, never count a
        transient (no futile 5-retry loop) and never delete (the row self-heals
        when the device re-registers). The gateway is never even contacted."""
        _configure_apns(settings)
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.APNS, token="x"
        )
        ch.encrypted_settings = '{"token": "x"}'  # raw string, as after rotation
        cm = _client_returning(mock.Mock(status_code=200))
        with (
            mock.patch.object(push.apns, "_apns_auth_token", return_value="tok"),
            mock.patch.object(push.apns, "_apns_client", return_value=cm),
        ):
            result = push.send_apns(_items(ch), "thread-x")

        assert result.delivered == 0
        assert result.transient == 0
        cm.post.assert_not_called()
        assert models.Channel.objects.filter(id=ch.id).exists()


# ---------------------------------------------------------------------------
# FCM sender  (httpx mocked)
# ---------------------------------------------------------------------------


def _configure_fcm(settings):
    settings.PUSH_ENABLED = True
    settings.PUSH_FCM_CREDENTIALS = '{"type": "service_account"}'
    settings.PUSH_FCM_PROJECT_ID = "my-project"


class TestFcmSender:
    """The FCM sender: delivery, stale-device removal, and transient handling."""

    def test_noop_when_not_configured(self, settings, push_caplog):
        """Without FCM credentials the sender delivers nothing — and warns
        once per process (not per notification) instead of dropping silently."""
        settings.PUSH_ENABLED = True
        settings.PUSH_FCM_CREDENTIALS = None
        push.common.warn_gateway_unconfigured.cache_clear()
        ch = _push_channel(factories.UserFactory(), platform=PushPlatformChoices.FCM)
        assert push.send_fcm(_items(ch), "thread-x").delivered == 0
        assert push.send_fcm(_items(ch), "thread-x").delivered == 0
        warnings = [
            r for r in push_caplog.records if "gateway not configured" in r.getMessage()
        ]
        assert len(warnings) == 1
        assert "'fcm'" in warnings[0].getMessage()

    def test_success(self, settings):
        """A 200 from FCM delivers a high-priority message with the token and
        stringified data."""
        _configure_fcm(settings)
        ch = _push_channel(
            factories.UserFactory(),
            platform=PushPlatformChoices.FCM,
            token="fcm-ok",
        )
        cm = _client_returning(mock.Mock(status_code=200))
        with (
            mock.patch.object(push.fcm, "_fcm_access_token", return_value="ya29"),
            mock.patch.object(push.fcm, "_fcm_client", return_value=cm),
        ):
            result = push.send_fcm(
                _items(ch, payload={"type": "new_message", "unread_count": 3}), "t-1"
            )

        assert result.delivered == 1
        assert models.Channel.objects.filter(id=ch.id).exists()
        body = cm.post.call_args.kwargs["json"]["message"]
        assert body["token"] == "fcm-ok"
        assert body["data"]["unread_count"] == "3"
        # Sent high-priority so a dozing device still wakes.
        assert body["android"]["priority"] == "high"

    def test_attaches_content_free_notification(self, settings):
        """The FCM message carries an OS-localized notification block so Android
        shows a banner when the app is killed — loc-keys + badge only, never
        the message content."""
        _configure_fcm(settings)
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.FCM, token="fcm-a"
        )
        cm = _client_returning(mock.Mock(status_code=200))
        with (
            mock.patch.object(push.fcm, "_fcm_access_token", return_value="ya29"),
            mock.patch.object(push.fcm, "_fcm_client", return_value=cm),
        ):
            push.send_fcm(
                _items(
                    ch,
                    payload={
                        "type": "new_message",
                        "unread_count": 5,
                        "subject": "leak?",
                    },
                ),
                "t-1",
            )
        notif = cm.post.call_args.kwargs["json"]["message"]["android"]["notification"]
        assert notif["title_loc_key"] == push.FCM_TITLE_LOC_KEY
        assert notif["body_loc_key"] == push.FCM_BODY_LOC_KEY
        assert notif["notification_count"] == 5
        # Contract with the Capacitor app (ANDROID_NOTIFICATION_CHANNEL_ID in
        # features/native/push.ts) and the manifest meta-data: without this
        # channel Android renders on the DEFAULT-importance fallback (no
        # heads-up).
        assert notif["channel_id"] == push.FCM_ANDROID_CHANNEL_ID == "new_messages"
        # No literal content anywhere in the notification block.
        assert "leak?" not in str(notif)

    def test_unregistered_removes_channel(self, settings):
        """An UNREGISTERED status is a dead token: the channel is deleted."""
        _configure_fcm(settings)
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.FCM, token="dead"
        )
        response = mock.Mock(status_code=404)
        response.json.return_value = {"error": {"status": "UNREGISTERED"}}
        with (
            mock.patch.object(push.fcm, "_fcm_access_token", return_value="ya29"),
            mock.patch.object(
                push.fcm, "_fcm_client", return_value=_client_returning(response)
            ),
        ):
            push.send_fcm(_items(ch), "t-1")

        assert not models.Channel.objects.filter(id=ch.id).exists()

    def test_invalid_argument_keeps_channel(self, settings):
        """INVALID_ARGUMENT may mean a bad *request*, not a dead token — so the
        device must NOT be deleted (else one payload bug wipes the fleet)."""
        _configure_fcm(settings)
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.FCM, token="ok"
        )
        response = mock.Mock(status_code=400, text="invalid argument")
        response.json.return_value = {
            "error": {
                "status": "INVALID_ARGUMENT",
                "details": [{"errorCode": "INVALID_ARGUMENT"}],
            }
        }
        with (
            mock.patch.object(push.fcm, "_fcm_access_token", return_value="ya29"),
            mock.patch.object(
                push.fcm, "_fcm_client", return_value=_client_returning(response)
            ),
        ):
            push.send_fcm(_items(ch), "t-1")

        assert models.Channel.objects.filter(id=ch.id).exists()

    def test_transient_5xx_is_counted(self, settings):
        """A 5xx is transient: counted for retry and the channel is kept."""
        _configure_fcm(settings)
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.FCM, token="t5xx"
        )
        response = mock.Mock(status_code=503, text="unavailable")
        response.json.return_value = {}
        with (
            mock.patch.object(push.fcm, "_fcm_access_token", return_value="ya29"),
            mock.patch.object(
                push.fcm, "_fcm_client", return_value=_client_returning(response)
            ),
        ):
            result = push.send_fcm(_items(ch), "t-1")

        assert result.transient == 1
        assert models.Channel.objects.filter(id=ch.id).exists()

    def test_client_setup_failure_is_transient(self, settings):
        """A client-setup failure fails the whole batch as transient (so the
        task retries) instead of propagating — same contract as APNs."""
        _configure_fcm(settings)
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.FCM, token="boom"
        )
        with (
            mock.patch.object(push.fcm, "_fcm_access_token", return_value="ya29"),
            mock.patch.object(
                push.fcm, "_fcm_client", side_effect=RuntimeError("no client")
            ),
        ):
            result = push.send_fcm(_items(ch), "t-1")

        assert result == push.PushResult(0, 1)
        assert models.Channel.objects.filter(id=ch.id).exists()

    def test_access_token_is_cached_across_calls(self, settings):
        """The OAuth access token is minted once and reused within its TTL."""
        cache.clear()
        _configure_fcm(settings)
        creds = mock.Mock(token="ya29-tok")
        with mock.patch(
            "core.services.push.fcm.service_account.Credentials.from_service_account_info",
            return_value=creds,
        ) as from_info:
            first = push.fcm._fcm_access_token()
            second = push.fcm._fcm_access_token()

        assert first == second == "ya29-tok"
        from_info.assert_called_once()

    def test_unreadable_settings_are_skipped_not_transient(self, settings):
        """An undecryptable device (raw string ``encrypted_settings`` after a
        Fernet key rotation) is skipped without contacting FCM, without counting
        a transient, and without deleting the row — the same permanent-skip
        contract the APNs and Web Push senders follow."""
        _configure_fcm(settings)
        ch = _push_channel(
            factories.UserFactory(), platform=PushPlatformChoices.FCM, token="x"
        )
        ch.encrypted_settings = '{"token": "x"}'  # raw string, as after rotation
        cm = _client_returning(mock.Mock(status_code=200))
        with (
            mock.patch.object(push.fcm, "_fcm_access_token", return_value="ya29"),
            mock.patch.object(push.fcm, "_fcm_client", return_value=cm),
        ):
            result = push.send_fcm(_items(ch), "t-1")

        assert result.delivered == 0
        assert result.transient == 0
        cm.post.assert_not_called()
        assert models.Channel.objects.filter(id=ch.id).exists()


# ---------------------------------------------------------------------------
# Web Push sender  (py-vapid + http-ece mocked; delivery via SSRFSafeSession)
# ---------------------------------------------------------------------------


def _configure_webpush(settings):
    settings.PUSH_ENABLED = True
    settings.PUSH_VAPID_PRIVATE_KEY = "vapid-key"
    settings.PUSH_VAPID_SUBJECT = "mailto:ops@example.com"


def _vapid_mock():
    vapid = mock.Mock()
    vapid.sign.return_value = {"Authorization": "vapid t=x,k=y"}
    return vapid


def _ssrf_session_returning(response):
    """A SSRFSafeSession mock whose .post returns ``response``."""
    session = mock.Mock()
    session.post.return_value = response
    return session


class TestWebpushSender:
    """Web Push via py-vapid + http-ece, delivered through SSRFSafeSession."""

    def _web_channel(self):
        return _push_channel(
            factories.UserFactory(),
            platform=PushPlatformChoices.WEB,
            token="https://push.example/ep",
            keys=_WEB_KEYS,
        )

    def test_success(self, settings):
        """A 201 delivers the encrypted (aes128gcm) payload to the endpoint."""
        _configure_webpush(settings)
        ch = self._web_channel()
        session = _ssrf_session_returning(mock.Mock(status_code=201))
        with (
            mock.patch.object(push.webpush, "_load_vapid", return_value=_vapid_mock()),
            mock.patch("http_ece.encrypt", return_value=b"ciphertext"),
            mock.patch.object(push.webpush, "SSRFSafeSession", return_value=session),
        ):
            result = push.send_webpush(_items(ch), "t-1")

        assert result.delivered == 1
        assert models.Channel.objects.filter(id=ch.id).exists()
        session.post.assert_called_once()
        assert session.post.call_args.kwargs["data"] == b"ciphertext"
        assert session.post.call_args.kwargs["headers"]["content-encoding"] == (
            "aes128gcm"
        )

    def test_gone_removes_channel(self, settings):
        """A 410 Gone from the push service deletes the subscription."""
        _configure_webpush(settings)
        ch = self._web_channel()
        session = _ssrf_session_returning(mock.Mock(status_code=410))
        with (
            mock.patch.object(push.webpush, "_load_vapid", return_value=_vapid_mock()),
            mock.patch("http_ece.encrypt", return_value=b"ciphertext"),
            mock.patch.object(push.webpush, "SSRFSafeSession", return_value=session),
        ):
            push.send_webpush(_items(ch), "t-1")

        assert not models.Channel.objects.filter(id=ch.id).exists()

    def test_ssrf_blocked_endpoint_is_kept(self, settings):
        """An endpoint that resolves to an internal/blocked address is refused
        by the SSRF guard — nothing is delivered and the channel is NOT deleted."""
        _configure_webpush(settings)
        ch = self._web_channel()
        session = mock.Mock()
        session.post.side_effect = push.SSRFValidationError("blocked")
        with (
            mock.patch.object(push.webpush, "_load_vapid", return_value=_vapid_mock()),
            mock.patch("http_ece.encrypt", return_value=b"ciphertext"),
            mock.patch.object(push.webpush, "SSRFSafeSession", return_value=session),
        ):
            result = push.send_webpush(_items(ch), "t-1")

        assert result.delivered == 0
        assert models.Channel.objects.filter(id=ch.id).exists()

    def test_transient_5xx_is_counted(self, settings):
        """A 5xx is transient: counted for retry and the channel is kept."""
        _configure_webpush(settings)
        ch = self._web_channel()
        session = _ssrf_session_returning(mock.Mock(status_code=503))
        with (
            mock.patch.object(push.webpush, "_load_vapid", return_value=_vapid_mock()),
            mock.patch("http_ece.encrypt", return_value=b"ciphertext"),
            mock.patch.object(push.webpush, "SSRFSafeSession", return_value=session),
        ):
            result = push.send_webpush(_items(ch), "t-1")

        assert result.transient == 1
        assert models.Channel.objects.filter(id=ch.id).exists()

    def test_undeliverable_keys_are_stale_not_transient(self, settings):
        """A channel whose keys decrypt fine but fail encryption (malformed
        base64 / non-P-256 point) is a PERMANENT failure: it must be deleted
        (stale), never counted transient — a transient here would retry the
        poison channel forever, once per inbound message. The network is never
        even reached."""
        _configure_webpush(settings)
        ch = self._web_channel()
        session = _ssrf_session_returning(mock.Mock(status_code=201))
        with (
            mock.patch.object(push.webpush, "_load_vapid", return_value=_vapid_mock()),
            mock.patch("http_ece.encrypt", side_effect=ValueError("bad point")),
            mock.patch.object(push.webpush, "SSRFSafeSession", return_value=session),
        ):
            result = push.send_webpush(_items(ch), "t-1")

        assert result.transient == 0
        assert result.delivered == 0
        session.post.assert_not_called()
        assert not models.Channel.objects.filter(id=ch.id).exists()

    def test_malformed_subject_disables_webpush(self, settings):
        """A VAPID subject that isn't a mailto:/https: URI would be rejected by
        the push service (401), so we treat it as not-configured and no-op."""
        _configure_webpush(settings)
        settings.PUSH_VAPID_SUBJECT = "ops@example.com"  # missing mailto:
        ch = self._web_channel()
        session = _ssrf_session_returning(mock.Mock(status_code=201))
        with (
            mock.patch.object(push.webpush, "_load_vapid", return_value=_vapid_mock()),
            mock.patch("http_ece.encrypt", return_value=b"ciphertext"),
            mock.patch.object(push.webpush, "SSRFSafeSession", return_value=session),
        ):
            result = push.send_webpush(_items(ch), "t-1")

        assert result.delivered == 0
        session.post.assert_not_called()

    def test_ttl_header_is_one_day(self, settings):
        """The TTL header instructs the push service to hold for one day."""
        _configure_webpush(settings)
        ch = self._web_channel()
        session = _ssrf_session_returning(mock.Mock(status_code=201))
        with (
            mock.patch.object(push.webpush, "_load_vapid", return_value=_vapid_mock()),
            mock.patch("http_ece.encrypt", return_value=b"ciphertext"),
            mock.patch.object(push.webpush, "SSRFSafeSession", return_value=session),
        ):
            push.send_webpush(_items(ch), "t-1")

        assert session.post.call_args.kwargs["headers"]["ttl"] == str(
            push.WEBPUSH_TTL_SECONDS
        )

    def test_unreadable_settings_are_skipped_not_transient(self, settings):
        """An undecryptable subscription (raw string ``encrypted_settings``
        after a Fernet key rotation) is skipped: the keys read back as ``None``,
        so it never reaches encryption or the network, is never counted
        transient, and is never deleted — matching the APNs/FCM contract."""
        _configure_webpush(settings)
        ch = self._web_channel()
        ch.encrypted_settings = '{"token": "https://push.example/ep"}'  # raw string
        session = _ssrf_session_returning(mock.Mock(status_code=201))
        with (
            mock.patch.object(push.webpush, "_load_vapid", return_value=_vapid_mock()),
            mock.patch("http_ece.encrypt", return_value=b"ciphertext"),
            mock.patch.object(push.webpush, "SSRFSafeSession", return_value=session),
        ):
            result = push.send_webpush(_items(ch), "t-1")

        assert result.delivered == 0
        assert result.transient == 0
        session.post.assert_not_called()
        assert models.Channel.objects.filter(id=ch.id).exists()


# ---------------------------------------------------------------------------
# Stale-device deactivation circuit-breaker
# ---------------------------------------------------------------------------


class TestStaleDeactivation:
    """The stale-device deletion circuit-breaker (ratio + rolling-window guards)."""

    def _android_channels(self, n):
        user = factories.UserFactory()
        return [
            _push_channel(user, platform=PushPlatformChoices.FCM, token=f"t{i}")
            for i in range(n)
        ]

    def test_single_device_is_deleted(self):
        """A lone stale device is deleted (nothing to ratio-check against)."""
        (ch,) = self._android_channels(1)
        assert push.common._deactivate_stale_channels([ch], 1, platform="fcm") == 1
        assert not models.Channel.objects.filter(id=ch.id).exists()

    def test_minority_stale_is_deleted(self):
        """A small fraction of stale devices is below the ratio → deleted."""
        chans = self._android_channels(5)
        # 1 of 5 stale → below the ratio → really deleted.
        assert push.common._deactivate_stale_channels(chans[:1], 5, platform="fcm") == 1
        assert models.Channel.objects.filter(type=ChannelTypes.PUSH).count() == 4

    def test_mass_stale_is_refused(self):
        """A mass stale report looks systemic → the breaker refuses to delete."""
        chans = self._android_channels(5)
        # All 5 reported stale at once → looks systemic → refuse to delete any.
        assert push.common._deactivate_stale_channels(chans, 5, platform="fcm") == 0
        assert models.Channel.objects.filter(type=ChannelTypes.PUSH).count() == 5

    def test_rolling_window_caps_one_off_deletions(self):
        """The per-notification path deletes one device at a time (no batch to
        ratio-check), so a rolling per-platform window stops a runaway fault from
        wiping a fleet one delete at a time."""
        cache.clear()
        deleted = 0
        with mock.patch.object(push.common, "STALE_DELETE_WINDOW_LIMIT", 3):
            # Each call passes the ratio guard (1 of 1) but counts toward the
            # rolling window; once the cap is hit, further deletes are refused.
            for _ in range(5):
                (ch,) = self._android_channels(1)
                deleted += push.common._deactivate_stale_channels(
                    [ch], 1, platform="fcm"
                )
        assert deleted == 3


# ---------------------------------------------------------------------------
# VAPID public-key derivation (offline, via the management command)
# ---------------------------------------------------------------------------


def _generate_vapid_keypair():
    """Return ``(private_pem, expected_public_b64url)`` for a fresh P-256 key."""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    raw = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    expected = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return pem, expected


def _vapid_private_raw_and_public():
    """Return ``(private_raw_b64url, expected_public_b64url)`` for a fresh key."""
    key = ec.generate_private_key(ec.SECP256R1())
    scalar = key.private_numbers().private_value.to_bytes(32, "big")
    private_raw = base64.urlsafe_b64encode(scalar).rstrip(b"=").decode("ascii")
    raw = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    expected = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return private_raw, expected


class TestDeriveVapidPublicKey:
    """Offline derivation of the VAPID public key from the private key."""

    def test_derives_the_matching_public_key(self):
        """A PEM private key derives to its matching public key."""
        pem, expected = _generate_vapid_keypair()
        assert push.derive_vapid_public_key(pem) == expected

    def test_derives_from_base64url_raw_key(self):
        """The base64url raw private key is accepted (fallback to from_raw)."""
        private_raw, expected = _vapid_private_raw_and_public()
        assert push.derive_vapid_public_key(private_raw) == expected

    def test_returns_none_on_invalid_pem(self):
        """An unparseable key yields None rather than raising."""
        assert push.derive_vapid_public_key("not-a-key") is None


class TestDeriveVapidPublicKeyCommand:
    """The ``derive_vapid_public_key`` management command (print + --verify)."""

    def test_prints_key_from_settings(self, settings):
        """The command derives the public key from the configured private key."""
        pem, expected = _generate_vapid_keypair()
        settings.PUSH_VAPID_PRIVATE_KEY = pem
        out = StringIO()
        call_command("derive_vapid_public_key", stdout=out, stderr=StringIO())
        assert out.getvalue().strip() == expected

    def test_private_key_argument_overrides_settings(self, settings):
        """The --private-key argument takes precedence over settings."""
        pem, expected = _generate_vapid_keypair()
        settings.PUSH_VAPID_PRIVATE_KEY = None
        out = StringIO()
        call_command(
            "derive_vapid_public_key",
            "--private-key",
            pem,
            stdout=out,
            stderr=StringIO(),
        )
        assert out.getvalue().strip() == expected

    def test_errors_without_a_private_key(self, settings):
        """The command errors out when no private key is available."""
        settings.PUSH_VAPID_PRIVATE_KEY = None
        with pytest.raises(CommandError):
            call_command("derive_vapid_public_key")

    def test_verify_passes_on_matching_pair(self, settings):
        """--verify confirms a private/public key pair that matches."""
        pem, expected = _generate_vapid_keypair()
        settings.PUSH_VAPID_PRIVATE_KEY = pem
        settings.PUSH_VAPID_PUBLIC_KEY = expected
        out = StringIO()
        call_command("derive_vapid_public_key", "--verify", stdout=out)
        assert "matches" in out.getvalue()

    def test_verify_fails_on_mismatch(self, settings):
        """--verify errors when the configured public key does not match."""
        pem, _expected = _generate_vapid_keypair()
        settings.PUSH_VAPID_PRIVATE_KEY = pem
        settings.PUSH_VAPID_PUBLIC_KEY = "wrong-key"
        with pytest.raises(CommandError, match="does NOT match"):
            call_command("derive_vapid_public_key", "--verify")

    def test_verify_fails_when_public_key_unset(self, settings):
        """--verify errors when no public key is configured to check against."""
        pem, _expected = _generate_vapid_keypair()
        settings.PUSH_VAPID_PRIVATE_KEY = pem
        settings.PUSH_VAPID_PUBLIC_KEY = None
        with pytest.raises(CommandError, match="not set"):
            call_command("derive_vapid_public_key", "--verify")


class TestGenerateVapidPrivateKeyCommand:
    """The ``generate_vapid_private_key`` management command."""

    def test_prints_a_consistent_base64url_keypair(self):
        """The generated key is base64url, loadable, and derives to the public
        key printed in the guidance — a matched pair."""
        out, err = StringIO(), StringIO()
        call_command("generate_vapid_private_key", stdout=out, stderr=err)

        private_b64 = out.getvalue().strip()
        # The printed private key is base64url and loadable (fallback path), and
        # derives to the public key echoed in the guidance — a matched pair.
        derived = push.derive_vapid_public_key(private_b64)
        assert derived
        assert f"PUSH_VAPID_PUBLIC_KEY={derived}" in err.getvalue()

    def test_generates_a_distinct_key_each_run(self):
        """Each invocation generates a fresh, distinct private key."""
        first, second = StringIO(), StringIO()
        call_command("generate_vapid_private_key", stdout=first, stderr=StringIO())
        call_command("generate_vapid_private_key", stdout=second, stderr=StringIO())
        assert first.getvalue().strip() != second.getvalue().strip()
