"""Authentication Backends for the messages core app."""

import logging
import re

from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from django.utils.translation import gettext_lazy as _

import requests
from mozilla_django_oidc.auth import (
    OIDCAuthenticationBackend as MozillaOIDCAuthenticationBackend,
)

from core.enums import MailboxRoleChoices
from core.models import (
    Contact,
    DuplicateEmailError,
    Mailbox,
    MailboxAccess,
    MailDomain,
    User,
)

logger = logging.getLogger(__name__)


class OIDCAuthenticationBackend(MozillaOIDCAuthenticationBackend):
    """Custom OpenID Connect (OIDC) Authentication Backend.

    This class overrides the default OIDC Authentication Backend to accommodate differences
    in the User and Identity models, and handles signed and/or encrypted UserInfo response.
    """

    def get_userinfo(self, access_token, id_token, payload):
        """Return user details dictionary.

        Parameters:
        - access_token (str): The access token.
        - id_token (str): The id token (unused).
        - payload (dict): The token payload (unused).

        Note: The id_token and payload parameters are unused in this implementation,
        but were kept to preserve base method signature.

        Note: It handles signed and/or encrypted UserInfo Response. It is required by
        Agent Connect, which follows the OIDC standard. It forces us to override the
        base method, which deal with 'application/json' response.

        Returns:
        - dict: User details dictionary obtained from the OpenID Connect user endpoint.
        """

        user_response = requests.get(
            self.OIDC_OP_USER_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
            verify=self.get_settings("OIDC_VERIFY_SSL", True),
            timeout=self.get_settings("OIDC_TIMEOUT", None),
            proxies=self.get_settings("OIDC_PROXY", None),
        )
        user_response.raise_for_status()

        try:
            userinfo = user_response.json()
        except ValueError:
            try:
                userinfo = self.verify_token(user_response.text)
            except Exception as e:
                raise SuspiciousOperation(
                    _("Invalid response format or token verification failed")
                ) from e

        return userinfo

    def verify_claims(self, claims):
        """
        Verify the presence of essential claims and the "sub" (which is mandatory as defined
        by the OIDC specification) to decide if authentication should be allowed.
        """
        essential_claims = settings.USER_OIDC_ESSENTIAL_CLAIMS
        missing_claims = [claim for claim in essential_claims if claim not in claims]

        if missing_claims:
            logger.error("Missing essential claims: %s", missing_claims)
            return False

        return True

    def get_or_create_user(self, access_token, id_token, payload):
        """Return a User based on userinfo. Create a new user if no match is found."""

        user_info = self.get_userinfo(access_token, id_token, payload)

        if not self.verify_claims(user_info):
            raise SuspiciousOperation("Claims verification failed.")

        sub = user_info["sub"]
        email = user_info.get("email")

        # Get user's full name from OIDC fields defined in settings
        full_name = self.compute_full_name(user_info)
        short_name = user_info.get(settings.USER_OIDC_FIELD_TO_SHORTNAME)

        claims = {
            "email": email,
            "full_name": full_name,
            "short_name": short_name,
        }

        try:
            user = User.objects.get_user_by_sub_or_email(sub, email)
        except DuplicateEmailError as err:
            raise SuspiciousOperation(err.message) from err

        self.create_testdomain()

        if user:
            if not user.is_active:
                raise SuspiciousOperation(_("User account is disabled"))
            self.update_user_if_needed(user, claims)

        elif self.should_create_user(email):
            user = User.objects.create(sub=sub, password="!", **claims)  # noqa: S106

        if user:
            self.autojoin_mailbox(user)
            return user

        return None

    def compute_full_name(self, user_info):
        """Compute user's full name based on OIDC fields in settings."""
        name_fields = settings.USER_OIDC_FIELDS_TO_FULLNAME
        full_name = " ".join(
            user_info[field] for field in name_fields if user_info.get(field)
        )
        return full_name or None

    def update_user_if_needed(self, user, claims):
        """Update user claims if they have changed."""
        has_changed = any(
            value and value != getattr(user, key) for key, value in claims.items()
        )
        if has_changed:
            updated_claims = {key: value for key, value in claims.items() if value}
            self.UserModel.objects.filter(id=user.id).update(**updated_claims)

    def create_testdomain(self):
        """Create the test domain if it doesn't exist."""

        # Create the test domain if it doesn't exist
        if settings.MESSAGES_TESTDOMAIN:
            MailDomain.objects.get_or_create(
                name=settings.MESSAGES_TESTDOMAIN,
                defaults={"oidc_autojoin": True, "identity_sync": True},
            )

    def should_create_user(self, email):
        """Check if a user should be created based on the email address."""

        if not email:
            return False

        # With this setting, we always create a user locally
        if self.get_settings("OIDC_CREATE_USER", True):
            return True

        # MESSAGES_TESTDOMAIN_MAPPING_BASEDOMAIN is a special case of autojoin
        testdomain_mapped_email = self.get_testdomain_mapped_email(email)
        if testdomain_mapped_email:
            return True

        # If the email address ends with a domain that has autojoin enabled
        if MailDomain.objects.filter(
            name=email.split("@")[1], oidc_autojoin=True
        ).exists():
            return True

        # Don't create a user locally
        return False

    def get_testdomain_mapped_email(self, email):
        """If it exists, return the mapped email address for the test domain."""
        if not settings.MESSAGES_TESTDOMAIN or not email:
            return None

        # Check if the email address ends with the test domain
        if not re.search(
            r"[@\.]"
            + re.escape(settings.MESSAGES_TESTDOMAIN_MAPPING_BASEDOMAIN)
            + r"$",
            email,
        ):
            return None

        # <x.y@z.base.domain> => <x.y-z@test.domain>
        prefix = email.split("@")[1][
            : -len(settings.MESSAGES_TESTDOMAIN_MAPPING_BASEDOMAIN) - 1
        ]
        return (
            email.split("@")[0]
            + ("-" + prefix if prefix else "")
            + "@"
            + settings.MESSAGES_TESTDOMAIN
        )

    def autojoin_mailbox(self, user):
        """Setup autojoin mailbox for user."""

        email = self.get_testdomain_mapped_email(user.email)
        if not email and user.email:
            # TODO aliases?
            if MailDomain.objects.filter(
                name=user.email.split("@")[1], oidc_autojoin=True
            ).exists():
                email = user.email

        if not email:
            return

        maildomain = MailDomain.objects.get(name=email.split("@")[1])

        # Create a mailbox for the user if missing
        mailbox, _ = Mailbox.objects.get_or_create(
            local_part=email.split("@")[0],
            domain=maildomain,
        )

        # Create an admin mailbox access for the user if needed
        mailbox_access, _ = MailboxAccess.objects.get_or_create(
            mailbox=mailbox,
            user=user,
            defaults={"role": MailboxRoleChoices.ADMIN},
        )
        if mailbox_access.role != MailboxRoleChoices.ADMIN:
            mailbox_access.role = MailboxRoleChoices.ADMIN
            mailbox_access.save()

        contact, _ = Contact.objects.get_or_create(
            email=email,
            mailbox=mailbox,
            defaults={"name": user.full_name or email.split("@")[0]},
        )
        mailbox.contact = contact
        mailbox.save()
        # if not created and contact.mailbox != mailbox:
        #     contact.mailbox = mailbox
        #     contact.save()
