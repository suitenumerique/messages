"""Authentication Backends for the messages core app."""

import logging
import re

from django.conf import settings
from django.core.exceptions import SuspiciousOperation

from lasuite.oidc_login.backends import (
    OIDCAuthenticationBackend as LaSuiteOIDCAuthenticationBackend,
)

from core.entitlements import EntitlementsUnavailableError, get_user_entitlements
from core.enums import MailboxRoleChoices, MailDomainAccessRoleChoices
from core.models import (
    Contact,
    DuplicateEmailError,
    Mailbox,
    MailboxAccess,
    MailDomain,
    MailDomainAccess,
    User,
)

logger = logging.getLogger(__name__)


class OIDCAuthenticationBackend(LaSuiteOIDCAuthenticationBackend):
    """Custom OpenID Connect (OIDC) Authentication Backend.

    This class overrides the default OIDC Authentication Backend to accommodate differences
    in the User and Identity models, and handles signed and/or encrypted UserInfo response.
    """

    def get_or_create_user(self, access_token, id_token, payload):
        """
        Return a User based on userinfo. Create a new user if no match is found.

        Args:
          access_token (str): The access token.
          id_token (str): The ID token.
          payload (dict): The user payload.

        Returns:
          User: An existing or newly created User instance.

        Raises:
          Exception: Raised when user creation is not allowed and no existing user is found.

        """
        _user_created = False
        user_info = self.get_userinfo(access_token, id_token, payload)
        self._user_info = user_info  # pylint: disable=attribute-defined-outside-init

        if not self.verify_claims(user_info):
            msg = "Claims verification failed"
            raise SuspiciousOperation(msg)

        sub = user_info["sub"]
        if not sub:
            raise SuspiciousOperation(
                "User info contained no recognizable user identification"
            )

        email = user_info.get("email")

        claims = {
            self.OIDC_USER_SUB_FIELD: sub,
            "email": email,
        }
        claims.update(**self.get_extra_claims(user_info))

        # if sub is absent, try matching on email
        user = self.get_existing_user(sub, email)
        self.create_testdomain()

        if user:
            if not user.is_active:
                raise SuspiciousOperation("User account is disabled")
            self.update_user_if_needed(user, claims)

        elif self.should_create_user(email):
            user = self.create_user(claims)
            _user_created = True

        self.post_get_or_create_user(user, claims, _user_created)
        return user

    def post_get_or_create_user(self, user, claims, _user_created):
        """Post-get or create user."""
        if user:
            self.autojoin_mailbox(user)
            self._sync_entitlements(user)

    def _sync_entitlements(self, user):
        """Fetch user entitlements and sync MailDomainAccess ADMIN records.

        Called on every login. Uses force_refresh=True to reset the cache.
        If the entitlements backend is unavailable, existing accesses are preserved.
        """
        user_info = getattr(self, "_user_info", None)

        try:
            entitlements = get_user_entitlements(
                user.sub, user.email, user_info=user_info, force_refresh=True
            )
        except EntitlementsUnavailableError:
            logger.warning("Entitlements service unavailable during login")
            return

        admin_domains = entitlements.get("can_admin_maildomains")
        if admin_domains is None:
            # Backend doesn't support this field (e.g. dummy), skip sync
            return

        if not isinstance(admin_domains, (list, tuple, set)):
            logger.warning(
                "Invalid type for can_admin_maildomains: %s, skipping sync",
                type(admin_domains).__name__,
            )
            return

        # Resolve domain names to MailDomain objects that exist in DB
        entitled_domains = list(MailDomain.objects.filter(name__in=admin_domains))
        entitled_domain_ids = {d.id for d in entitled_domains}

        # Get current ADMIN accesses for this user
        existing_accesses = MailDomainAccess.objects.filter(
            user=user, role=MailDomainAccessRoleChoices.ADMIN
        )
        existing_domain_ids = set(
            existing_accesses.values_list("maildomain_id", flat=True)
        )

        # Optimistic path: if already in sync, skip all writes
        if entitled_domain_ids == existing_domain_ids:
            return

        # Add new accesses
        for domain in entitled_domains:
            if domain.id not in existing_domain_ids:
                MailDomainAccess.objects.update_or_create(
                    user=user,
                    maildomain=domain,
                    defaults={"role": MailDomainAccessRoleChoices.ADMIN},
                )

        # Remove stale accesses (domains not in the entitled list)
        stale_domain_ids = existing_domain_ids - entitled_domain_ids
        if stale_domain_ids:
            MailDomainAccess.objects.filter(
                user=user,
                maildomain_id__in=stale_domain_ids,
                role=MailDomainAccessRoleChoices.ADMIN,
            ).delete()

    def get_extra_claims(self, user_info):
        """Get extra claims."""
        return {
            "full_name": self.compute_full_name(user_info),
        }

    def get_existing_user(self, sub, email):
        """Get an existing user by sub or email."""
        try:
            return User.objects.get_user_by_sub_or_email(sub, email)
        except DuplicateEmailError as err:
            raise SuspiciousOperation(err.message) from err

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
