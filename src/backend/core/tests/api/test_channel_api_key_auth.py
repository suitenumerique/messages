"""Tests for ChannelApiKeyAuthentication + HasChannelScope.

Covers the cross-cutting auth/permission layer directly, independent of any
specific endpoint. Endpoint-specific scope enforcement is tested in each
viewset's own test file (test_submit, test_provisioning_*, test_*_metrics).
"""
# pylint: disable=missing-function-docstring,missing-class-docstring,import-outside-toplevel

import hashlib
import uuid
from datetime import timedelta

from django.utils import timezone

import pytest

from core import models
from core.enums import ChannelApiKeyScope, ChannelScopeLevel
from core.factories import MailboxFactory, make_api_key_channel

SUBMIT_URL = "/api/v1.0/submit/"


def _make_channel(scopes=(ChannelApiKeyScope.MESSAGES_SEND.value,), **kwargs):
    """Wrapper around the shared factory pre-loaded with the auth-class
    test default scope (messages:send). Callers can still override
    ``scopes`` and any other kwarg."""
    return make_api_key_channel(scopes=scopes, **kwargs)


@pytest.mark.django_db
class TestChannelApiKeyAuth:
    """Direct edge-case coverage for the authentication class."""

    def test_missing_headers_returns_401(self, client):
        """No headers at all → DRF NotAuthenticated → 401."""
        response = client.post(SUBMIT_URL)
        assert response.status_code == 401

    def test_malformed_channel_id_returns_401(self, client):
        response = client.post(
            SUBMIT_URL,
            HTTP_X_CHANNEL_ID="not-a-uuid",
            HTTP_X_API_KEY="anything",
        )
        assert response.status_code == 401

    def test_wrong_secret_returns_401(self, client):
        channel, _plaintext = _make_channel()
        response = client.post(
            SUBMIT_URL,
            HTTP_X_CHANNEL_ID=str(channel.id),
            HTTP_X_API_KEY="not-the-real-secret",
        )
        assert response.status_code == 401

    def test_expired_key_returns_401(self, client):
        past = (timezone.now() - timedelta(days=1)).isoformat()
        channel, plaintext = _make_channel(extra_settings={"expires_at": past})
        response = client.post(
            SUBMIT_URL,
            HTTP_X_CHANNEL_ID=str(channel.id),
            HTTP_X_API_KEY=plaintext,
        )
        assert response.status_code == 401

    def test_non_api_key_channel_cannot_authenticate(self, client):
        """A widget/mta channel with a hash in encrypted_settings must not
        authenticate the api_key path — the authentication class filters by
        type='api_key' explicitly."""
        plaintext = f"msg_test_{uuid.uuid4().hex}"
        digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        channel = models.Channel(
            name="not-api-key",
            type="widget",
            scope_level=ChannelScopeLevel.GLOBAL,
            encrypted_settings={"api_key_hashes": [digest]},
            settings={"scopes": [ChannelApiKeyScope.MESSAGES_SEND.value]},
        )
        channel.save()
        response = client.post(
            SUBMIT_URL,
            HTTP_X_CHANNEL_ID=str(channel.id),
            HTTP_X_API_KEY=plaintext,
        )
        assert response.status_code == 401

    def test_last_used_at_updates_on_success(self, client):
        """A successful auth call updates last_used_at within the throttle window."""
        channel, plaintext = _make_channel()
        mailbox = MailboxFactory()
        assert channel.last_used_at is None

        # We don't need the call to succeed end-to-end for the submit endpoint.
        # We just need to reach the authentication class, which itself calls
        # mark_used() on success. Submit without a body → 400, but auth has
        # already passed by then.
        response = client.post(
            SUBMIT_URL,
            HTTP_X_CHANNEL_ID=str(channel.id),
            HTTP_X_API_KEY=plaintext,
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="to@x.test",
        )
        # Auth must have passed; the post-auth pipeline is allowed to
        # produce 202 (accepted), 400 (empty body / validation), or 403
        # (scope mismatch). 500 is NOT accepted — a server error here
        # would mask a real bug behind a "test still green" signal.
        assert response.status_code in (202, 400, 403)

        channel.refresh_from_db()
        # Only check that last_used_at moved past the None state when
        # the auth call succeeded (scopes include messages:send + global).
        assert channel.last_used_at is not None


@pytest.mark.django_db
class TestHasChannelScope:
    """Direct tests for HasChannelScope.has_permission."""

    def test_scope_present(self, rf):
        channel, _ = _make_channel(scopes=(ChannelApiKeyScope.MESSAGES_SEND.value,))
        from core.api.permissions import channel_scope

        perm_class = channel_scope(ChannelApiKeyScope.MESSAGES_SEND)
        request = rf.post("/")
        request.auth = channel
        assert perm_class().has_permission(request, None) is True

    def test_scope_absent(self, rf):
        channel, _ = _make_channel(scopes=(ChannelApiKeyScope.METRICS_READ.value,))
        from core.api.permissions import channel_scope

        perm_class = channel_scope(ChannelApiKeyScope.MESSAGES_SEND)
        request = rf.post("/")
        request.auth = channel
        assert perm_class().has_permission(request, None) is False

    def test_auth_not_a_channel(self, rf):
        from core.api.permissions import channel_scope

        perm_class = channel_scope(ChannelApiKeyScope.MESSAGES_SEND)
        request = rf.post("/")
        request.auth = None
        assert perm_class().has_permission(request, None) is False


@pytest.mark.django_db
class TestApiKeyCovers:
    """Channel.api_key_covers resource-scope helper."""

    def test_global_covers_everything(self):
        channel, _ = _make_channel(scope_level=ChannelScopeLevel.GLOBAL)
        mailbox = MailboxFactory()
        assert channel.api_key_covers(mailbox=mailbox) is True
        assert channel.api_key_covers(maildomain=mailbox.domain) is True

    def test_maildomain_covers_only_its_domain(self):
        mailbox = MailboxFactory()
        channel, _ = _make_channel(
            scope_level=ChannelScopeLevel.MAILDOMAIN,
            maildomain=mailbox.domain,
        )
        assert channel.api_key_covers(mailbox=mailbox) is True
        other = MailboxFactory()
        assert channel.api_key_covers(mailbox=other) is False

    def test_mailbox_covers_only_itself(self):
        mailbox = MailboxFactory()
        channel, _ = _make_channel(
            scope_level=ChannelScopeLevel.MAILBOX,
            mailbox=mailbox,
        )
        assert channel.api_key_covers(mailbox=mailbox) is True
        other = MailboxFactory()
        assert channel.api_key_covers(mailbox=other) is False
