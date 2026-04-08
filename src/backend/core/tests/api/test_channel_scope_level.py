"""Tests for Channel.scope_level defense-in-depth.

Every layer that is supposed to block a non-superadmin from creating a
scope_level=global row is exercised here. If the DB constraint is removed,
these tests still catch escalation at the ORM layer. If the ORM layer is
bypassed via a raw insert, the check constraint catches it.
"""
# pylint: disable=import-outside-toplevel,missing-function-docstring,too-many-lines

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse

import pytest

from core import models
from core.enums import ChannelScopeLevel, ChannelTypes
from core.factories import (
    MailboxAccessFactory,
    MailboxFactory,
    MailDomainFactory,
    UserFactory,
)

# -------------------------------------------------------------------------- #
# DB check constraint
# -------------------------------------------------------------------------- #


def _force_insert(**kwargs):
    """Create a Channel bypassing BaseModel.save() / full_clean().

    bulk_create skips save() and our ORM-level clean(), so the row is written
    straight to PostgreSQL and only the DB check constraint can reject it.
    This is what we want to exercise in this test class.
    """
    return models.Channel.objects.bulk_create([models.Channel(**kwargs)])


@pytest.mark.django_db
class TestScopeLevelCheckConstraint:
    """PostgreSQL must reject inconsistent scope_level/target combinations."""

    def test_global_with_mailbox_raises(self):
        mailbox = MailboxFactory()
        with transaction.atomic():
            with pytest.raises(IntegrityError):
                _force_insert(
                    name="bad-global",
                    type=ChannelTypes.API_KEY,
                    scope_level=ChannelScopeLevel.GLOBAL,
                    mailbox=mailbox,
                )

    def test_global_with_maildomain_raises(self):
        maildomain = MailDomainFactory()
        with transaction.atomic():
            with pytest.raises(IntegrityError):
                _force_insert(
                    name="bad-global2",
                    type=ChannelTypes.API_KEY,
                    scope_level=ChannelScopeLevel.GLOBAL,
                    maildomain=maildomain,
                )

    def test_mailbox_with_maildomain_raises(self):
        mailbox = MailboxFactory()
        maildomain = MailDomainFactory()
        with transaction.atomic():
            with pytest.raises(IntegrityError):
                _force_insert(
                    name="bad-mixed",
                    type=ChannelTypes.API_KEY,
                    scope_level=ChannelScopeLevel.MAILBOX,
                    mailbox=mailbox,
                    maildomain=maildomain,
                )

    def test_mailbox_without_mailbox_raises(self):
        with transaction.atomic():
            with pytest.raises(IntegrityError):
                _force_insert(
                    name="bad-empty-mailbox",
                    type=ChannelTypes.API_KEY,
                    scope_level=ChannelScopeLevel.MAILBOX,
                )

    def test_valid_global(self):
        c = models.Channel.objects.create(
            name="good-global",
            type=ChannelTypes.API_KEY,
            scope_level=ChannelScopeLevel.GLOBAL,
        )
        assert c.pk is not None

    def test_valid_mailbox(self):
        mailbox = MailboxFactory()
        c = models.Channel.objects.create(
            name="good-mailbox",
            type=ChannelTypes.API_KEY,
            scope_level=ChannelScopeLevel.MAILBOX,
            mailbox=mailbox,
        )
        assert c.pk is not None


# -------------------------------------------------------------------------- #
# Model clean() / full_clean
# -------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestScopeLevelCleanValidation:
    """Model-level validation: full_clean() (via validate_constraints, called
    automatically from BaseModel.save) rejects every illegal scope_level/target
    combination before the row reaches the DB."""

    def test_global_with_mailbox_full_clean(self):
        mailbox = MailboxFactory()
        channel = models.Channel(
            name="x",
            type=ChannelTypes.API_KEY,
            scope_level=ChannelScopeLevel.GLOBAL,
            mailbox=mailbox,
        )
        with pytest.raises(ValidationError):
            channel.full_clean()

    def test_global_with_maildomain_full_clean(self):
        maildomain = MailDomainFactory()
        channel = models.Channel(
            name="x",
            type=ChannelTypes.API_KEY,
            scope_level=ChannelScopeLevel.GLOBAL,
            maildomain=maildomain,
        )
        with pytest.raises(ValidationError):
            channel.full_clean()

    def test_maildomain_with_mailbox_full_clean(self):
        mailbox = MailboxFactory()
        maildomain = MailDomainFactory()
        channel = models.Channel(
            name="x",
            type=ChannelTypes.API_KEY,
            scope_level=ChannelScopeLevel.MAILDOMAIN,
            mailbox=mailbox,
            maildomain=maildomain,
        )
        with pytest.raises(ValidationError):
            channel.full_clean()

    def test_maildomain_without_maildomain_full_clean(self):
        channel = models.Channel(
            name="x",
            type=ChannelTypes.API_KEY,
            scope_level=ChannelScopeLevel.MAILDOMAIN,
        )
        with pytest.raises(ValidationError):
            channel.full_clean()

    def test_mailbox_without_mailbox_full_clean(self):
        channel = models.Channel(
            name="x",
            type=ChannelTypes.API_KEY,
            scope_level=ChannelScopeLevel.MAILBOX,
        )
        with pytest.raises(ValidationError):
            channel.full_clean()

    def test_mailbox_with_maildomain_full_clean(self):
        mailbox = MailboxFactory()
        maildomain = MailDomainFactory()
        channel = models.Channel(
            name="x",
            type=ChannelTypes.API_KEY,
            scope_level=ChannelScopeLevel.MAILBOX,
            mailbox=mailbox,
            maildomain=maildomain,
        )
        with pytest.raises(ValidationError):
            channel.full_clean()

    def test_user_without_user_full_clean(self):
        channel = models.Channel(
            name="x",
            type=ChannelTypes.API_KEY,
            scope_level=ChannelScopeLevel.USER,
        )
        with pytest.raises(ValidationError):
            channel.full_clean()

    def test_user_with_mailbox_full_clean(self):
        mailbox = MailboxFactory()
        user = UserFactory()
        channel = models.Channel(
            name="x",
            type=ChannelTypes.API_KEY,
            scope_level=ChannelScopeLevel.USER,
            user=user,
            mailbox=mailbox,
        )
        with pytest.raises(ValidationError):
            channel.full_clean()

    def test_save_triggers_full_clean(self):
        """BaseModel.save() runs full_clean() and rejects bad rows before the DB."""
        mailbox = MailboxFactory()
        bad = models.Channel(
            name="bad",
            type=ChannelTypes.API_KEY,
            scope_level=ChannelScopeLevel.GLOBAL,
            mailbox=mailbox,
        )
        with pytest.raises(ValidationError):
            bad.save()


# -------------------------------------------------------------------------- #
# DRF path: mailbox-nested viewset never exposes or writes non-mailbox scope
# -------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestChannelViewSetIsolation:  # pylint: disable=too-many-public-methods
    """The existing mailbox-nested ChannelViewSet only sees mailbox-scope rows
    and only creates mailbox-scope rows — no matter what the client sends."""

    def _url_list(self, mailbox):
        return reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})

    def test_list_excludes_global(self, api_client):
        user = UserFactory()
        mailbox = MailboxFactory()
        from core.enums import MailboxRoleChoices

        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.ADMIN)
        api_client.force_authenticate(user=user)

        # Create a mailbox-scope row that should be visible...
        mailbox_channel = models.Channel.objects.create(
            name="mbx",
            type=ChannelTypes.API_KEY,
            scope_level=ChannelScopeLevel.MAILBOX,
            mailbox=mailbox,
            settings={"scopes": ["messages:send"]},
        )
        # ...and a global-scope row that must NOT be visible.
        models.Channel.objects.create(
            name="glb",
            type=ChannelTypes.API_KEY,
            scope_level=ChannelScopeLevel.GLOBAL,
        )

        response = api_client.get(self._url_list(mailbox))
        assert response.status_code == 200, response.content
        ids = {row["id"] for row in response.data}
        assert str(mailbox_channel.id) in ids
        # No global channel should appear.
        assert all(row.get("scope_level") == "mailbox" for row in response.data)

    def test_post_body_scope_level_is_ignored(self, api_client):
        """A client sending scope_level=global in the body gets scope_level=mailbox."""
        import hashlib
        import uuid

        from core.enums import MailboxRoleChoices

        user = UserFactory()
        mailbox = MailboxFactory()
        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.ADMIN)
        api_client.force_authenticate(user=user)

        response = api_client.post(
            self._url_list(mailbox),
            data={
                "name": f"test-{uuid.uuid4().hex[:6]}",
                "type": "api_key",
                "scope_level": "global",  # read-only — must be ignored
                "settings": {"scopes": ["messages:send"]},
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        created = models.Channel.objects.get(pk=response.data["id"])
        assert created.scope_level == ChannelScopeLevel.MAILBOX
        assert created.mailbox_id == mailbox.id
        # Response should include plaintext api_key once and the row id.
        assert response.data.get("api_key", "").startswith("msgk_")
        assert response.data["id"] == str(created.id)
        # The returned plaintext must hash to the single stored entry in
        # encrypted_settings.api_key_hashes.
        expected_hash = hashlib.sha256(
            response.data["api_key"].encode("utf-8")
        ).hexdigest()
        assert created.encrypted_settings["api_key_hashes"] == [expected_hash]

    def test_mailbox_admin_cannot_request_global_only_scope(self, api_client):
        """maildomains:create requires scope_level=global and is rejected here."""
        import uuid

        from core.enums import MailboxRoleChoices

        user = UserFactory()
        mailbox = MailboxFactory()
        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.ADMIN)
        api_client.force_authenticate(user=user)

        response = api_client.post(
            self._url_list(mailbox),
            data={
                "name": f"test-{uuid.uuid4().hex[:6]}",
                "type": "api_key",
                "settings": {"scopes": ["maildomains:create"]},
            },
            format="json",
        )
        assert response.status_code == 400, response.content

    def test_mailbox_admin_cannot_request_metrics_read_scope(self, api_client):
        """metrics:read is in CHANNEL_API_KEY_SCOPES_GLOBAL_ONLY too."""
        import uuid

        from core.enums import MailboxRoleChoices

        user = UserFactory()
        mailbox = MailboxFactory()
        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.ADMIN)
        api_client.force_authenticate(user=user)

        response = api_client.post(
            self._url_list(mailbox),
            data={
                "name": f"test-{uuid.uuid4().hex[:6]}",
                "type": "api_key",
                "settings": {"scopes": ["metrics:read"]},
            },
            format="json",
        )
        assert response.status_code == 400, response.content

    def test_post_body_mailbox_field_is_ignored(self, api_client):
        """A client trying to bind to a different mailbox via the body is
        silently overridden by the URL mailbox (mailbox is read-only)."""
        import uuid

        from core.enums import MailboxRoleChoices

        user = UserFactory()
        my_mailbox = MailboxFactory()
        other_mailbox = MailboxFactory()
        MailboxAccessFactory(
            mailbox=my_mailbox, user=user, role=MailboxRoleChoices.ADMIN
        )
        api_client.force_authenticate(user=user)

        response = api_client.post(
            self._url_list(my_mailbox),
            data={
                "name": f"test-{uuid.uuid4().hex[:6]}",
                "type": "api_key",
                "mailbox": str(other_mailbox.id),  # read-only — ignored
                "settings": {"scopes": ["messages:send"]},
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        created = models.Channel.objects.get(pk=response.data["id"])
        assert created.mailbox_id == my_mailbox.id
        assert created.scope_level == ChannelScopeLevel.MAILBOX

    def test_post_body_maildomain_field_is_ignored(self, api_client):
        """Same protection for the maildomain FK in the body."""
        import uuid

        from core import factories
        from core.enums import MailboxRoleChoices

        user = UserFactory()
        mailbox = MailboxFactory()
        unrelated_domain = factories.MailDomainFactory()
        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.ADMIN)
        api_client.force_authenticate(user=user)

        response = api_client.post(
            self._url_list(mailbox),
            data={
                "name": f"test-{uuid.uuid4().hex[:6]}",
                "type": "api_key",
                "maildomain": str(unrelated_domain.id),  # read-only — ignored
                "settings": {"scopes": ["messages:send"]},
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        created = models.Channel.objects.get(pk=response.data["id"])
        assert created.maildomain_id is None
        assert created.scope_level == ChannelScopeLevel.MAILBOX

    def test_create_response_includes_id(self, api_client):
        """The create response carries the row id (= the X-Channel-Id value
        on subsequent api_key calls). No separate channel_id field."""
        import uuid

        from core.enums import MailboxRoleChoices

        user = UserFactory()
        mailbox = MailboxFactory()
        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.ADMIN)
        api_client.force_authenticate(user=user)

        response = api_client.post(
            self._url_list(mailbox),
            data={
                "name": f"key-{uuid.uuid4().hex[:6]}",
                "type": "api_key",
                "settings": {"scopes": ["messages:send"]},
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        assert "id" in response.data
        assert "channel_id" not in response.data  # no duplicate field

    # ----- PATCH escalation: scope validation must run on partial updates -- #

    def _detail_url(self, mailbox, channel):
        return reverse(
            "mailbox-channels-detail",
            kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
        )

    def _create_with_send_scope(self, api_client, mailbox):
        from core.enums import MailboxRoleChoices

        admin = UserFactory()
        MailboxAccessFactory(mailbox=mailbox, user=admin, role=MailboxRoleChoices.ADMIN)
        api_client.force_authenticate(user=admin)
        response = api_client.post(
            self._url_list(mailbox),
            data={
                "name": "legit",
                "type": "api_key",
                "settings": {"scopes": ["messages:send"]},
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        return models.Channel.objects.get(pk=response.data["id"])

    def test_patch_cannot_grant_global_only_scope(self, api_client):
        """PATCH escalation: a mailbox admin cannot grant a global-only
        scope by PATCHing settings on an existing api_key channel.

        This is the airtight test for the bug where
        ``_validate_api_key_scopes`` returned early when ``type`` wasn't in
        attrs (the typical PATCH shape), letting the new scopes through
        unvalidated."""
        mailbox = MailboxFactory()
        channel = self._create_with_send_scope(api_client, mailbox)

        response = api_client.patch(
            self._detail_url(mailbox, channel),
            data={"settings": {"scopes": ["maildomains:create"]}},
            format="json",
        )
        assert response.status_code == 400, response.content

        # And the row was NOT mutated.
        channel.refresh_from_db()
        assert channel.settings["scopes"] == ["messages:send"]

    def test_patch_cannot_inject_unknown_scope(self, api_client):
        """PATCH must also reject scope strings outside ChannelApiKeyScope."""
        mailbox = MailboxFactory()
        channel = self._create_with_send_scope(api_client, mailbox)

        response = api_client.patch(
            self._detail_url(mailbox, channel),
            data={"settings": {"scopes": ["messages:send", "evil:do_anything"]}},
            format="json",
        )
        assert response.status_code == 400, response.content
        channel.refresh_from_db()
        assert channel.settings["scopes"] == ["messages:send"]

    def test_patch_cannot_empty_scopes(self, api_client):
        """PATCH replacing settings to one without scopes must fail —
        api_key channels require settings.scopes."""
        mailbox = MailboxFactory()
        channel = self._create_with_send_scope(api_client, mailbox)

        response = api_client.patch(
            self._detail_url(mailbox, channel),
            data={"settings": {"expires_at": "2030-01-01T00:00:00Z"}},
            format="json",
        )
        assert response.status_code == 400, response.content
        channel.refresh_from_db()
        # Original scopes preserved.
        assert channel.settings["scopes"] == ["messages:send"]

    def test_patch_legitimate_scope_change_works(self, api_client):
        """The fix must NOT break legit narrowing PATCHes — a mailbox
        admin can still PATCH from one valid mailbox-allowed scope set
        to another."""
        mailbox = MailboxFactory()
        channel = self._create_with_send_scope(api_client, mailbox)

        response = api_client.patch(
            self._detail_url(mailbox, channel),
            data={
                "settings": {
                    "scopes": ["messages:send", "mailboxes:read"],
                }
            },
            format="json",
        )
        # mailboxes:read isn't in CHANNEL_API_KEY_SCOPES_GLOBAL_ONLY, so
        # it's grantable on a mailbox-scope channel.
        assert response.status_code == 200, response.content
        channel.refresh_from_db()
        assert sorted(channel.settings["scopes"]) == sorted(
            ["messages:send", "mailboxes:read"]
        )

    def test_patch_rename_does_not_require_settings(self, api_client):
        """A pure rename PATCH must NOT trip the api_key validators."""
        mailbox = MailboxFactory()
        channel = self._create_with_send_scope(api_client, mailbox)

        response = api_client.patch(
            self._detail_url(mailbox, channel),
            data={"name": "renamed"},
            format="json",
        )
        assert response.status_code == 200, response.content
        channel.refresh_from_db()
        assert channel.name == "renamed"
        assert channel.settings["scopes"] == ["messages:send"]

    # ----- Regression locks: read-only fields stay read-only on PATCH ----- #
    #
    # These tests assert behaviors that are CURRENTLY airtight (read-only
    # fields, the constraint, etc.). They exist so that any future change
    # that accidentally makes one of these writable trips a failing test.
    # Do NOT delete these as "redundant" — they are the regression net.

    def test_patch_scope_level_in_body_is_ignored(self, api_client):
        """PATCH cannot escalate scope_level via the body."""
        mailbox = MailboxFactory()
        channel = self._create_with_send_scope(api_client, mailbox)

        response = api_client.patch(
            self._detail_url(mailbox, channel),
            data={"scope_level": "global"},
            format="json",
        )
        assert response.status_code == 200, response.content
        channel.refresh_from_db()
        assert channel.scope_level == ChannelScopeLevel.MAILBOX

    def test_patch_mailbox_in_body_is_ignored(self, api_client):
        """PATCH cannot rebind a channel to a different mailbox."""
        mailbox = MailboxFactory()
        other_mailbox = MailboxFactory()
        channel = self._create_with_send_scope(api_client, mailbox)

        response = api_client.patch(
            self._detail_url(mailbox, channel),
            data={"mailbox": str(other_mailbox.id)},
            format="json",
        )
        assert response.status_code == 200, response.content
        channel.refresh_from_db()
        assert channel.mailbox_id == mailbox.id

    def test_patch_maildomain_in_body_is_ignored(self, api_client):
        """PATCH cannot rebind a channel to a different maildomain."""
        from core import factories

        mailbox = MailboxFactory()
        unrelated_domain = factories.MailDomainFactory()
        channel = self._create_with_send_scope(api_client, mailbox)

        response = api_client.patch(
            self._detail_url(mailbox, channel),
            data={"maildomain": str(unrelated_domain.id)},
            format="json",
        )
        assert response.status_code == 200, response.content
        channel.refresh_from_db()
        assert channel.maildomain_id is None

    def test_patch_user_in_body_is_ignored(self, api_client):
        """PATCH cannot rebind the creator/target user via the body."""
        mailbox = MailboxFactory()
        channel = self._create_with_send_scope(api_client, mailbox)
        original_user_id = channel.user_id
        attacker = UserFactory()

        response = api_client.patch(
            self._detail_url(mailbox, channel),
            data={"user": str(attacker.id)},
            format="json",
        )
        assert response.status_code == 200, response.content
        channel.refresh_from_db()
        assert channel.user_id == original_user_id

    def test_create_encrypted_settings_in_body_is_ignored(self, api_client):
        """``encrypted_settings`` is not in ChannelSerializer.fields, so the
        whole top-level field is silently dropped — the row's
        encrypted_settings is what the server generated, never what the
        caller sent."""
        from core.enums import MailboxRoleChoices

        user = UserFactory()
        mailbox = MailboxFactory()
        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.ADMIN)
        api_client.force_authenticate(user=user)

        response = api_client.post(
            self._url_list(mailbox),
            data={
                "name": "ess",
                "type": "api_key",
                "settings": {"scopes": ["messages:send"]},
                "encrypted_settings": {"api_key_hashes": ["evil" * 16]},
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        created = models.Channel.objects.get(pk=response.data["id"])
        # The server-generated hash is what landed; the caller's "evil"
        # injection was dropped because encrypted_settings isn't a field.
        assert created.encrypted_settings["api_key_hashes"] != ["evil" * 16]

    def test_patch_encrypted_settings_in_body_is_ignored(self, api_client):
        """Same protection on PATCH."""
        mailbox = MailboxFactory()
        channel = self._create_with_send_scope(api_client, mailbox)
        original_hashes = list(channel.encrypted_settings["api_key_hashes"])

        response = api_client.patch(
            self._detail_url(mailbox, channel),
            data={"encrypted_settings": {"api_key_hashes": ["evil" * 16]}},
            format="json",
        )
        assert response.status_code == 200, response.content
        channel.refresh_from_db()
        assert channel.encrypted_settings["api_key_hashes"] == original_hashes

    # ----- Real holes that need fixing -------------------------------------- #

    def test_patch_settings_with_api_key_hashes_is_rejected(self, api_client):
        """A mailbox admin must NOT be able to inject their own api_key_hash
        by smuggling it into ``settings``. The serializer's
        _move_sensitive_settings hook would otherwise extract it from
        settings and write it into encrypted_settings, letting the attacker
        choose the hash that authenticates the channel."""
        import hashlib

        mailbox = MailboxFactory()
        channel = self._create_with_send_scope(api_client, mailbox)
        legit_hashes = list(channel.encrypted_settings["api_key_hashes"])
        evil_plaintext = "evil_known_secret"
        evil_hash = hashlib.sha256(evil_plaintext.encode()).hexdigest()

        response = api_client.patch(
            self._detail_url(mailbox, channel),
            data={
                "settings": {
                    "scopes": ["messages:send"],
                    "api_key_hashes": [evil_hash],
                }
            },
            format="json",
        )
        assert response.status_code == 400, response.content
        channel.refresh_from_db()
        # The attacker's hash MUST NOT have been written to encrypted_settings.
        assert channel.encrypted_settings["api_key_hashes"] == legit_hashes

    def test_create_settings_with_api_key_hashes_is_rejected(self, api_client):
        """Same defense on CREATE — even though _generate_api_key_material
        currently overwrites the hash, the request itself should be
        rejected so the caller's intent is loud, not silently dropped."""
        from core.enums import MailboxRoleChoices

        user = UserFactory()
        mailbox = MailboxFactory()
        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.ADMIN)
        api_client.force_authenticate(user=user)

        response = api_client.post(
            self._url_list(mailbox),
            data={
                "name": "evil-create",
                "type": "api_key",
                "settings": {
                    "scopes": ["messages:send"],
                    "api_key_hashes": ["a" * 64],
                },
            },
            format="json",
        )
        assert response.status_code == 400, response.content

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["api_key", "widget"])
    def test_patch_type_is_immutable(self, api_client):
        """The ``type`` of an existing channel is immutable from DRF.

        Allowing PATCH ``type=widget → api_key`` would let a mailbox admin
        sneak around the create-time scope checks; allowing the reverse
        would let them strand a row whose type the auth class no longer
        recognizes. We override FEATURE_MAILBOX_ADMIN_CHANNELS to include
        BOTH types so this test exercises type-mutability itself, not the
        (separate) feature-flag allowlist check.

        DRF's standard behavior for read-only fields is silent drop on
        write — same as the other read-only FKs (mailbox, scope_level,
        etc.). The PATCH succeeds with 200 but the type is unchanged.
        """
        mailbox = MailboxFactory()
        channel = self._create_with_send_scope(api_client, mailbox)
        assert channel.type == "api_key"

        response = api_client.patch(
            self._detail_url(mailbox, channel),
            data={"type": "widget"},
            format="json",
        )
        assert response.status_code == 200, response.content
        channel.refresh_from_db()
        assert channel.type == "api_key"

    def test_patch_disallowed_type_in_body_is_silently_dropped(self, api_client):
        """A PATCH with a type that isn't even in the allowlist still has
        no effect — type is read-only on update, so DRF drops the field
        silently regardless of whether the value would have been allowed."""
        mailbox = MailboxFactory()
        channel = self._create_with_send_scope(api_client, mailbox)

        response = api_client.patch(
            self._detail_url(mailbox, channel),
            data={"type": "mta"},  # not in FEATURE_MAILBOX_ADMIN_CHANNELS
            format="json",
        )
        assert response.status_code == 200, response.content
        channel.refresh_from_db()
        assert channel.type == "api_key"


@pytest.mark.django_db
class TestUserChannelViewSet:
    """The /users/me/channels/ viewset for personal scope_level=user channels."""

    URL = "/api/v1.0/users/me/channels/"

    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.post(
            self.URL,
            data={
                "name": "personal",
                "type": "api_key",
                "settings": {"scopes": ["messages:send"]},
            },
            format="json",
        )
        assert response.status_code in (401, 403)

    def test_create_personal_api_key(self, api_client):
        from core.enums import MailboxRoleChoices

        user = UserFactory()
        mailbox = MailboxFactory()
        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.SENDER)
        api_client.force_authenticate(user=user)

        response = api_client.post(
            self.URL,
            data={
                "name": "my-key",
                "type": "api_key",
                "settings": {"scopes": ["messages:send"]},
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        assert "id" in response.data
        assert response.data.get("api_key", "").startswith("msgk_")

        created = models.Channel.objects.get(pk=response.data["id"])
        assert created.scope_level == ChannelScopeLevel.USER
        assert created.user_id == user.id
        assert created.mailbox_id is None
        assert created.maildomain_id is None

    def test_personal_key_only_lists_own_channels(self, api_client):
        alice = UserFactory()
        bob = UserFactory()
        # Create one personal channel for each via the model directly
        models.Channel.objects.create(
            name="alice-key",
            type="api_key",
            scope_level=ChannelScopeLevel.USER,
            user=alice,
            settings={"scopes": ["messages:send"]},
        )
        models.Channel.objects.create(
            name="bob-key",
            type="api_key",
            scope_level=ChannelScopeLevel.USER,
            user=bob,
            settings={"scopes": ["messages:send"]},
        )

        api_client.force_authenticate(user=alice)
        response = api_client.get(self.URL)
        assert response.status_code == 200
        names = {row["name"] for row in response.data}
        assert names == {"alice-key"}

    def test_personal_key_cannot_request_global_only_scope(self, api_client):
        user = UserFactory()
        api_client.force_authenticate(user=user)
        response = api_client.post(
            self.URL,
            data={
                "name": "no-globals",
                "type": "api_key",
                "settings": {"scopes": ["maildomains:create"]},
            },
            format="json",
        )
        assert response.status_code == 400, response.content

    def test_personal_key_covers_user_mailboxes(self, db):  # pylint: disable=unused-argument
        """A user-scope channel's api_key_covers respects MailboxAccess."""
        from core.enums import MailboxRoleChoices

        user = UserFactory()
        accessible_mailbox = MailboxFactory()
        unrelated_mailbox = MailboxFactory()
        MailboxAccessFactory(
            mailbox=accessible_mailbox,
            user=user,
            role=MailboxRoleChoices.SENDER,
        )

        channel = models.Channel.objects.create(
            name="personal",
            type="api_key",
            scope_level=ChannelScopeLevel.USER,
            user=user,
            settings={"scopes": ["messages:send"]},
        )

        assert channel.api_key_covers(mailbox=accessible_mailbox) is True
        assert channel.api_key_covers(mailbox=unrelated_mailbox) is False

    def test_personal_key_role_check_rejects_viewer(self, db):  # pylint: disable=unused-argument
        """A viewer-only user-scope key must NOT pass api_key_covers when
        the endpoint requires a SENDER-or-better role. This is the regression
        test for the viewer-can-submit hole."""
        from core.enums import MAILBOX_ROLES_CAN_SEND, MailboxRoleChoices

        user = UserFactory()
        mailbox = MailboxFactory()
        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.VIEWER)

        channel = models.Channel.objects.create(
            name="viewer-personal",
            type="api_key",
            scope_level=ChannelScopeLevel.USER,
            user=user,
            settings={"scopes": ["messages:send"]},
        )

        # Without role kwarg the helper still says True (any access exists).
        assert channel.api_key_covers(mailbox=mailbox) is True
        # With the SENDER-or-better requirement it must say False.
        assert (
            channel.api_key_covers(
                mailbox=mailbox, mailbox_roles=MAILBOX_ROLES_CAN_SEND
            )
            is False
        )

    # ----------- cross-user isolation: retrieve / update / destroy -------- #

    def _make_personal(self, user, name="key"):
        return models.Channel.objects.create(
            name=name,
            type="api_key",
            scope_level=ChannelScopeLevel.USER,
            user=user,
            settings={"scopes": ["messages:send"]},
            encrypted_settings={"api_key_hashes": ["x" * 64]},
        )

    def _detail_url(self, channel):
        return f"{self.URL}{channel.id}/"

    def test_retrieve_other_users_channel_returns_404(self, api_client):
        alice = UserFactory()
        bob = UserFactory()
        bob_channel = self._make_personal(bob)

        api_client.force_authenticate(user=alice)
        response = api_client.get(self._detail_url(bob_channel))
        assert response.status_code == 404

    def test_update_other_users_channel_returns_404(self, api_client):
        alice = UserFactory()
        bob = UserFactory()
        bob_channel = self._make_personal(bob)

        api_client.force_authenticate(user=alice)
        response = api_client.patch(
            self._detail_url(bob_channel),
            data={"name": "stolen"},
            format="json",
        )
        assert response.status_code == 404
        bob_channel.refresh_from_db()
        assert bob_channel.name == "key"  # untouched

    def test_destroy_other_users_channel_returns_404(self, api_client):
        alice = UserFactory()
        bob = UserFactory()
        bob_channel = self._make_personal(bob)

        api_client.force_authenticate(user=alice)
        response = api_client.delete(self._detail_url(bob_channel))
        assert response.status_code == 404
        assert models.Channel.objects.filter(pk=bob_channel.pk).exists()

    def test_owner_can_destroy_own_channel(self, api_client):
        user = UserFactory()
        channel = self._make_personal(user)

        api_client.force_authenticate(user=user)
        response = api_client.delete(self._detail_url(channel))
        assert response.status_code == 204
        assert not models.Channel.objects.filter(pk=channel.pk).exists()

    def test_owner_can_rename_own_channel(self, api_client):
        user = UserFactory()
        channel = self._make_personal(user)

        api_client.force_authenticate(user=user)
        response = api_client.patch(
            self._detail_url(channel),
            data={"name": "renamed"},
            format="json",
        )
        assert response.status_code == 200
        channel.refresh_from_db()
        assert channel.name == "renamed"

    def test_user_creating_multiple_personal_channels(self, api_client):
        """A user can hold several personal api_keys at the same time."""
        user = UserFactory()
        api_client.force_authenticate(user=user)

        ids = set()
        for i in range(3):
            response = api_client.post(
                self.URL,
                data={
                    "name": f"key-{i}",
                    "type": "api_key",
                    "settings": {"scopes": ["messages:send"]},
                },
                format="json",
            )
            assert response.status_code == 201, response.content
            ids.add(response.data["id"])
        assert len(ids) == 3
        assert (
            models.Channel.objects.filter(
                user=user, scope_level=ChannelScopeLevel.USER
            ).count()
            == 3
        )

    def test_user_in_post_body_is_ignored(self, api_client):
        """Trying to bind a personal channel to another user via the body
        is silently overridden by the request.user."""
        alice = UserFactory()
        bob = UserFactory()
        api_client.force_authenticate(user=alice)

        response = api_client.post(
            self.URL,
            data={
                "name": "stolen-target",
                "type": "api_key",
                "user": str(bob.id),  # read-only — ignored
                "settings": {"scopes": ["messages:send"]},
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        created = models.Channel.objects.get(pk=response.data["id"])
        assert created.user_id == alice.id

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["api_key", "webhook"])
    def test_personal_webhook_channel(self, api_client):
        """Webhooks ARE creatable as personal channels — once the type is
        enabled in FEATURE_MAILBOX_ADMIN_CHANNELS. The type is intentionally
        left out of the production default until the outbound webhook
        delivery pipeline lands; this test enables it locally so the
        model + serializer + url/events validation path stays covered."""
        user = UserFactory()
        api_client.force_authenticate(user=user)

        response = api_client.post(
            self.URL,
            data={
                "name": "personal-webhook",
                "type": "webhook",
                "settings": {
                    "url": "https://hook.example.com/me",
                    "events": ["message.received"],
                },
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        # No HMAC secret generation yet — that scaffolding lands with the
        # delivery pipeline. The response carries the row id only.
        assert "hmac_secret" not in response.data
        created = models.Channel.objects.get(pk=response.data["id"])
        assert created.scope_level == ChannelScopeLevel.USER
        assert created.user_id == user.id


@pytest.mark.django_db
class TestUserDeleteCascade:
    """Deleting a User must remove their personal scope_level=user channels
    and *only* those — never blanket-cascade via the FK."""

    def test_user_delete_removes_only_user_scope_channels(self):
        user = UserFactory()
        mailbox = MailboxFactory()

        # The user owns one personal channel.
        personal = models.Channel.objects.create(
            name="personal",
            type="api_key",
            scope_level=ChannelScopeLevel.USER,
            user=user,
            settings={"scopes": ["messages:send"]},
        )
        # And there's an unrelated mailbox-scope channel that should
        # survive the user's deletion.
        mailbox_channel = models.Channel.objects.create(
            name="mbx",
            type="api_key",
            scope_level=ChannelScopeLevel.MAILBOX,
            mailbox=mailbox,
            settings={"scopes": ["messages:send"]},
        )

        user.delete()

        assert not models.Channel.objects.filter(pk=personal.pk).exists()
        assert models.Channel.objects.filter(pk=mailbox_channel.pk).exists()

    def test_user_delete_with_no_personal_channels_succeeds(self):
        """Deleting a user without any personal channels works even though
        the pre_delete handler has nothing to clean up."""
        user = UserFactory()
        user.delete()
        # Reaching this line is the assertion.


@pytest.mark.django_db
class TestRegenerateApiKey:
    """The regenerate-api-key action: single-active replace, never append.

    DRF's only rotation flow. Smooth (dual-active) rotation would happen
    in the Django admin or a future CLI command.
    """

    SUBMIT_URL = "/api/v1.0/submit/"

    @staticmethod
    def _hash(plaintext):
        import hashlib

        return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()

    # ---- mailbox-nested viewset ----------------------------------------- #

    def _mailbox_url(self, mailbox, channel):
        return reverse(
            "mailbox-channels-regenerate-api-key",
            kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
        )

    def _create_mailbox_api_key(self, api_client, mailbox):
        from core.enums import MailboxRoleChoices

        admin = UserFactory()
        MailboxAccessFactory(mailbox=mailbox, user=admin, role=MailboxRoleChoices.ADMIN)
        api_client.force_authenticate(user=admin)
        response = api_client.post(
            reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id}),
            data={
                "name": "rotatable",
                "type": "api_key",
                "settings": {"scopes": ["messages:send"]},
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        return admin, response.data["id"], response.data["api_key"]

    def test_regenerate_replaces_secret(self, api_client):
        mailbox = MailboxFactory()
        _admin, channel_id, original_plaintext = self._create_mailbox_api_key(
            api_client, mailbox
        )
        original_hashes = list(
            models.Channel.objects.get(pk=channel_id).encrypted_settings[
                "api_key_hashes"
            ]
        )
        assert original_hashes == [self._hash(original_plaintext)]

        response = api_client.post(
            self._mailbox_url(mailbox, models.Channel.objects.get(pk=channel_id))
        )
        assert response.status_code == 200, response.content
        new_plaintext = response.data["api_key"]
        assert new_plaintext.startswith("msgk_")
        assert new_plaintext != original_plaintext
        assert response.data["id"] == str(channel_id)

        stored = models.Channel.objects.get(pk=channel_id).encrypted_settings[
            "api_key_hashes"
        ]
        # Single-active: exactly one hash, the new one. The old one is gone.
        assert len(stored) == 1
        assert stored == [self._hash(new_plaintext)]

    def test_regenerate_invalidates_old_secret_end_to_end(self, api_client, client):
        """After regenerate, the old plaintext immediately fails auth on
        a real endpoint."""
        from core.enums import MailboxRoleChoices

        mailbox = MailboxFactory()
        admin = UserFactory()
        MailboxAccessFactory(mailbox=mailbox, user=admin, role=MailboxRoleChoices.ADMIN)
        api_client.force_authenticate(user=admin)
        # Create a key with messages:send scope so we can hit /submit/.
        create_url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        response = api_client.post(
            create_url,
            data={
                "name": "soon-rotated",
                "type": "api_key",
                "settings": {"scopes": ["messages:send"]},
            },
            format="json",
        )
        channel_id = response.data["id"]
        old_plaintext = response.data["api_key"]

        # Regenerate.
        response = api_client.post(
            self._mailbox_url(mailbox, models.Channel.objects.get(pk=channel_id))
        )
        assert response.status_code == 200
        new_plaintext = response.data["api_key"]

        # Old plaintext → /submit/ rejects with 401 (auth-layer failure).
        # We use the unauthenticated `client` to avoid the OIDC session
        # taking precedence over the api_key auth class.
        response = client.post(
            self.SUBMIT_URL,
            data=b"",
            content_type="message/rfc822",
            HTTP_X_CHANNEL_ID=str(channel_id),
            HTTP_X_API_KEY=old_plaintext,
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="x@example.com",
        )
        assert response.status_code == 401

        # New plaintext is accepted by the auth layer (the body is empty
        # so the view returns 400, but auth has already passed).
        response = client.post(
            self.SUBMIT_URL,
            data=b"",
            content_type="message/rfc822",
            HTTP_X_CHANNEL_ID=str(channel_id),
            HTTP_X_API_KEY=new_plaintext,
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="x@example.com",
        )
        assert response.status_code == 400  # empty body, but auth passed

    def test_regenerate_rejected_on_non_api_key_type(self, api_client):
        from core.enums import MailboxRoleChoices

        mailbox = MailboxFactory()
        admin = UserFactory()
        MailboxAccessFactory(mailbox=mailbox, user=admin, role=MailboxRoleChoices.ADMIN)
        api_client.force_authenticate(user=admin)

        widget = models.Channel.objects.create(
            name="widget",
            type="widget",
            scope_level=ChannelScopeLevel.MAILBOX,
            mailbox=mailbox,
            settings={"config": {"enabled": True}},
        )
        response = api_client.post(self._mailbox_url(mailbox, widget))
        assert response.status_code == 400, response.content

    def test_regenerate_unauthenticated_returns_403(self, api_client):
        mailbox = MailboxFactory()
        channel = models.Channel.objects.create(
            name="x",
            type="api_key",
            scope_level=ChannelScopeLevel.MAILBOX,
            mailbox=mailbox,
            settings={"scopes": ["messages:send"]},
            encrypted_settings={"api_key_hashes": ["x" * 64]},
        )
        response = api_client.post(self._mailbox_url(mailbox, channel))
        assert response.status_code in (401, 403)

    def test_regenerate_other_mailbox_admin_returns_403(self, api_client):
        from core.enums import MailboxRoleChoices

        mailbox_a = MailboxFactory()
        mailbox_b = MailboxFactory()
        admin_b = UserFactory()
        MailboxAccessFactory(
            mailbox=mailbox_b, user=admin_b, role=MailboxRoleChoices.ADMIN
        )
        api_client.force_authenticate(user=admin_b)

        channel_in_a = models.Channel.objects.create(
            name="not-yours",
            type="api_key",
            scope_level=ChannelScopeLevel.MAILBOX,
            mailbox=mailbox_a,
            settings={"scopes": ["messages:send"]},
            encrypted_settings={"api_key_hashes": ["x" * 64]},
        )
        # Use mailbox_a's URL but auth as admin_b → IsMailboxAdmin denies.
        response = api_client.post(self._mailbox_url(mailbox_a, channel_in_a))
        assert response.status_code in (403, 404)

    # ---- /users/me/channels/ -------------------------------------------- #

    def _user_url(self, channel):
        return reverse("user-channels-regenerate-api-key", kwargs={"pk": channel.id})

    def test_regenerate_personal_api_key(self, api_client):
        user = UserFactory()
        api_client.force_authenticate(user=user)
        # Mint a personal api_key first.
        response = api_client.post(
            "/api/v1.0/users/me/channels/",
            data={
                "name": "personal-rotatable",
                "type": "api_key",
                "settings": {"scopes": ["messages:send"]},
            },
            format="json",
        )
        assert response.status_code == 201
        channel_id = response.data["id"]
        original_plaintext = response.data["api_key"]

        response = api_client.post(
            self._user_url(models.Channel.objects.get(pk=channel_id))
        )
        assert response.status_code == 200, response.content
        new_plaintext = response.data["api_key"]
        assert new_plaintext != original_plaintext
        assert models.Channel.objects.get(pk=channel_id).encrypted_settings[
            "api_key_hashes"
        ] == [self._hash(new_plaintext)]

    def test_regenerate_other_users_personal_key_returns_404(self, api_client):
        alice = UserFactory()
        bob = UserFactory()
        bob_channel = models.Channel.objects.create(
            name="bob-personal",
            type="api_key",
            scope_level=ChannelScopeLevel.USER,
            user=bob,
            settings={"scopes": ["messages:send"]},
            encrypted_settings={"api_key_hashes": ["x" * 64]},
        )
        api_client.force_authenticate(user=alice)
        response = api_client.post(self._user_url(bob_channel))
        assert response.status_code == 404
        # Bob's secret was untouched.
        assert models.Channel.objects.get(pk=bob_channel.pk).encrypted_settings[
            "api_key_hashes"
        ] == ["x" * 64]
