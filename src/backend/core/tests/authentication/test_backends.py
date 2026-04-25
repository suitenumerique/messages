"""Unit tests for the Authentication Backends."""

import random
import re

from django.core.exceptions import SuspiciousOperation
from django.test.utils import override_settings

import pytest
import responses
from cryptography.fernet import Fernet
from lasuite.oidc_login.backends import get_oidc_refresh_token

from core import factories, models
from core.authentication.backends import OIDCAuthenticationBackend
from core.factories import UserFactory

pytestmark = pytest.mark.django_db


@override_settings(MESSAGES_TESTDOMAIN=None)
def test_authentication_getter_existing_user_no_email(monkeypatch):
    """
    If an existing user matches the user's info sub, the user should be returned.
    """

    klass = OIDCAuthenticationBackend()
    db_user = UserFactory()

    def get_userinfo_mocked(*args):
        return {"sub": db_user.sub}

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    user = klass.get_or_create_user(
        access_token="test-token", id_token=None, payload=None
    )

    assert user == db_user


@override_settings(MESSAGES_TESTDOMAIN=None)
def test_authentication_getter_existing_user_via_email(monkeypatch):
    """
    If an existing user doesn't match the sub but matches the email,
    the user should be returned.
    """

    klass = OIDCAuthenticationBackend()
    db_user = UserFactory()

    def get_userinfo_mocked(*args):
        return {"sub": "123", "email": db_user.email}

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    user = klass.get_or_create_user(
        access_token="test-token", id_token=None, payload=None
    )

    assert user == db_user


def test_authentication_getter_email_none(monkeypatch):
    """
    If no user is found with the sub and no email is provided, no user should be created (we need emails here!)
    """

    klass = OIDCAuthenticationBackend()
    UserFactory(email=None)

    def get_userinfo_mocked(*args):
        user_info = {"sub": "123"}
        if random.choice([True, False]):
            user_info["email"] = None
        return user_info

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    user = klass.get_or_create_user(
        access_token="test-token", id_token=None, payload=None
    )

    # Since the sub and email didn't match, it shouldn't create a new user
    assert models.User.objects.count() == 1
    assert user is None


@override_settings(
    OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION=False,
    OIDC_ALLOW_DUPLICATE_EMAILS=True,
    OIDC_CREATE_USER=True,
)
def test_authentication_getter_existing_user_no_fallback_to_email_allow_duplicate(
    monkeypatch,
):
    """
    When the "OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION" setting is set to False,
    the system should not match users by email, even if the email matches.
    """

    klass = OIDCAuthenticationBackend()
    db_user = UserFactory()

    def get_userinfo_mocked(*args):
        return {"sub": "123", "email": db_user.email}

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    user = klass.get_or_create_user(
        access_token="test-token", id_token=None, payload=None
    )

    # Since the sub doesn't match, it should create a new user
    assert models.User.objects.count() == 2
    assert user != db_user
    assert user.sub == "123"


@override_settings(
    OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION=False,
    OIDC_ALLOW_DUPLICATE_EMAILS=False,
    OIDC_CREATE_USER=True,
)
def test_authentication_getter_existing_user_no_fallback_to_email_no_duplicate(
    monkeypatch,
):
    """
    When the "OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION" setting is set to False,
    the system should not match users by email, even if the email matches.
    """

    klass = OIDCAuthenticationBackend()
    db_user = UserFactory()

    def get_userinfo_mocked(*args):
        return {"sub": "123", "email": db_user.email}

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    with pytest.raises(
        SuspiciousOperation,
        match=(
            "We couldn't find a user with this sub but the email is already associated "
            "with a registered user."
        ),
    ):
        klass.get_or_create_user(access_token="test-token", id_token=None, payload=None)

    # Since the sub doesn't match, it should not create a new user
    assert models.User.objects.count() == 1


@override_settings(
    MESSAGES_TESTDOMAIN=None,
    OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION=False,
    OIDC_ALLOW_DUPLICATE_EMAILS=False,
    OIDC_CREATE_USER=True,
)
def test_authentication_getter_claims_passwordless_stub_by_email(monkeypatch):
    """
    A user with no sub (an "invited" stub created via the access-grant API or
    the admin) should always be claimed on first OIDC login by email, even
    when OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION is disabled. Pre-existing
    accesses must remain attached to the same row.
    """

    klass = OIDCAuthenticationBackend()
    stub = UserFactory(sub=None, email="invitee@example.com", full_name=None)
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(user=stub, mailbox=mailbox)

    def get_userinfo_mocked(*args):
        return {"sub": "oidc-sub-xyz", "email": stub.email}

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    user = klass.get_or_create_user(
        access_token="test-token", id_token=None, payload=None
    )

    assert user.pk == stub.pk
    assert user.sub == "oidc-sub-xyz"
    assert models.User.objects.count() == 1
    # The pre-existing access still references the merged user.
    assert models.MailboxAccess.objects.filter(user=user, mailbox=mailbox).exists()


@override_settings(MESSAGES_TESTDOMAIN=None)
def test_authentication_getter_existing_user_with_email(monkeypatch):
    """
    When the user's info contains an email and targets an existing user,
    """
    klass = OIDCAuthenticationBackend()
    user = UserFactory(full_name="John Doe")

    def get_userinfo_mocked(*args):
        return {
            "sub": user.sub,
            "email": user.email,
            "first_name": "John",
            "last_name": "Doe",
        }

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    authenticated_user = klass.get_or_create_user(
        access_token="test-token", id_token=None, payload=None
    )

    assert user == authenticated_user


@override_settings(MESSAGES_TESTDOMAIN=None)
@pytest.mark.parametrize(
    "first_name, last_name, email",
    [
        ("Jack", "Doe", "john.doe@example.com"),
        ("John", "Duy", "john.doe@example.com"),
        ("John", "Doe", "jack.duy@example.com"),
        ("Jack", "Duy", "jack.duy@example.com"),
    ],
)
def test_authentication_getter_existing_user_change_fields_sub(
    first_name, last_name, email, monkeypatch
):
    """
    It should update the email or name fields on the user when they change
    and the user was identified by its "sub".
    """
    klass = OIDCAuthenticationBackend()
    user = UserFactory(full_name="John Doe", email="john.doe@example.com")

    def get_userinfo_mocked(*args):
        return {
            "sub": user.sub,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
        }

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    authenticated_user = klass.get_or_create_user(
        access_token="test-token", id_token=None, payload=None
    )

    assert user == authenticated_user
    user.refresh_from_db()
    assert user.email == email
    assert user.full_name == f"{first_name:s} {last_name:s}"


@override_settings(MESSAGES_TESTDOMAIN=None)
@pytest.mark.parametrize(
    "first_name, last_name, email",
    [
        ("Jack", "Doe", "john.doe@example.com"),
        ("John", "Duy", "john.doe@example.com"),
    ],
)
def test_authentication_getter_existing_user_change_fields_email(
    first_name, last_name, email, monkeypatch
):
    """
    It should update the name fields on the user when they change
    and the user was identified by its "email" as fallback.
    """
    klass = OIDCAuthenticationBackend()
    user = UserFactory(full_name="John Doe", email="john.doe@example.com")

    def get_userinfo_mocked(*args):
        return {
            "sub": "123",
            "email": user.email,
            "first_name": first_name,
            "last_name": last_name,
        }

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    authenticated_user = klass.get_or_create_user(
        access_token="test-token", id_token=None, payload=None
    )

    assert user == authenticated_user
    user.refresh_from_db()
    assert user.email == email
    assert user.full_name == f"{first_name:s} {last_name:s}"


def test_authentication_getter_new_user_no_email(monkeypatch):
    """
    If no user matches the user's info sub, a user shouldn't be created if it has no email
    """
    klass = OIDCAuthenticationBackend()

    def get_userinfo_mocked(*args):
        return {"sub": "123"}

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    user = klass.get_or_create_user(
        access_token="test-token", id_token=None, payload=None
    )

    assert models.User.objects.count() == 0
    assert user is None


@override_settings(MESSAGES_TESTDOMAIN="example.local")
def test_authentication_getter_new_user_with_email(monkeypatch):
    """
    If no user matches the user's info sub, a user should be created.
    User's email and name should be set on the identity.
    The "email" field on the User model should not be set as it is reserved for staff users.
    """
    klass = OIDCAuthenticationBackend()

    email = "messages@example.local"

    def get_userinfo_mocked(*args):
        return {"sub": "123", "email": email, "first_name": "John", "last_name": "Doe"}

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    user = klass.get_or_create_user(
        access_token="test-token", id_token=None, payload=None
    )

    assert user.sub == "123"
    assert user.email == email
    assert user.full_name == "John Doe"
    assert user.password.startswith("!")
    assert models.User.objects.count() == 1


def test_authentication_getter_existing_disabled_user_via_email(monkeypatch):
    """
    If an existing user does not match the sub but matches the email and is disabled,
    an error should be raised and a user should not be created.
    """

    klass = OIDCAuthenticationBackend()
    db_user = UserFactory(is_active=False)

    def get_userinfo_mocked(*args):
        return {
            "sub": "random",
            "email": db_user.email,
            "first_name": "John",
            "last_name": "Doe",
        }

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    with (
        pytest.raises(SuspiciousOperation, match="User account is disabled"),
    ):
        klass.get_or_create_user(access_token="test-token", id_token=None, payload=None)

    assert models.User.objects.count() == 1


@override_settings(
    OIDC_OP_USER_ENDPOINT="http://oidc.endpoint.test/userinfo",
    OIDC_USERINFO_ESSENTIAL_CLAIMS=["email", "last_name"],
    MESSAGES_TESTDOMAIN="testdomain.bzh",
    MESSAGES_TESTDOMAIN_MAPPING_BASEDOMAIN="gouv.fr",
)
def test_authentication_getter_new_user_with_testdomain(monkeypatch):
    """
    Check the TESTDOMAIN creation process
    """

    klass = OIDCAuthenticationBackend()

    def get_userinfo_mocked(*args):
        return {
            "email": "john.doe@sub.gouv.fr",
            "last_name": "Doe",
            "sub": "123",
        }

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    user = klass.get_or_create_user(
        access_token="test-token", id_token=None, payload=None
    )

    assert models.User.objects.filter(id=user.id).exists()

    assert user.sub == "123"
    assert user.full_name == "Doe"
    assert user.email == "john.doe@sub.gouv.fr"

    maildomain = models.MailDomain.objects.get(name="testdomain.bzh")
    mailbox = models.Mailbox.objects.get(local_part="john.doe-sub", domain=maildomain)

    assert models.Contact.objects.filter(
        email="john.doe-sub@testdomain.bzh", mailbox=mailbox
    ).exists()
    assert models.Mailbox.objects.filter(
        local_part="john.doe-sub", domain=maildomain
    ).exists()
    assert models.MailboxAccess.objects.filter(
        mailbox=mailbox,
        user=user,
        role=models.MailboxRoleChoices.ADMIN,
    ).exists()


@override_settings(
    OIDC_OP_USER_ENDPOINT="http://oidc.endpoint.test/userinfo",
    OIDC_USERINFO_ESSENTIAL_CLAIMS=["email", "last_name"],
    MESSAGES_TESTDOMAIN="testdomain.bzh",
    MESSAGES_TESTDOMAIN_MAPPING_BASEDOMAIN="gouv.fr",
)
def test_authentication_getter_new_user_with_testdomain_no_mapping(monkeypatch):
    """
    Check the TESTDOMAIN creation process when email doesn't match
    """

    klass = OIDCAuthenticationBackend()

    def get_userinfo_mocked(*args):
        return {
            "email": "john.doe@notgouv.fr",
            "last_name": "Doe",
            "sub": "123",
        }

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    user = klass.get_or_create_user(
        access_token="test-token", id_token=None, payload=None
    )

    assert user is None

    assert models.User.objects.count() == 0


@responses.activate
@override_settings(
    OIDC_OP_TOKEN_ENDPOINT="http://oidc.endpoint.test/token",
    OIDC_OP_USER_ENDPOINT="http://oidc.endpoint.test/userinfo",
    OIDC_OP_JWKS_ENDPOINT="http://oidc.endpoint.test/jwks",
    OIDC_STORE_ACCESS_TOKEN=True,
    OIDC_STORE_REFRESH_TOKEN=True,
    OIDC_STORE_REFRESH_TOKEN_KEY=Fernet.generate_key(),
    MESSAGES_TESTDOMAIN="example.local",
)
def test_authentication_session_tokens(monkeypatch, rf, settings):
    """
    Test that the session contains oidc_refresh_token and oidc_access_token after authentication.
    """
    klass = OIDCAuthenticationBackend()
    request = rf.get("/some-url", {"state": "test-state", "code": "test-code"})
    request.session = {}

    def verify_token_mocked(*args, **kwargs):
        return {"sub": "123", "email": "test@example.local"}

    monkeypatch.setattr(OIDCAuthenticationBackend, "verify_token", verify_token_mocked)

    responses.add(
        responses.POST,
        re.compile(settings.OIDC_OP_TOKEN_ENDPOINT),
        json={
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
        },
        status=200,
    )

    responses.add(
        responses.GET,
        re.compile(settings.OIDC_OP_USER_ENDPOINT),
        json={"sub": "123", "email": "test@example.local"},
        status=200,
    )

    user = klass.authenticate(
        request,
        code="test-code",
        nonce="test-nonce",
        code_verifier="test-code-verifier",
    )

    assert user is not None
    assert request.session["oidc_access_token"] == "test-access-token"
    assert get_oidc_refresh_token(request.session) == "test-refresh-token"
