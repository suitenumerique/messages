"""Authentication Backends for the messages core app."""

import logging

from django.core.exceptions import SuspiciousOperation
from django.core.exceptions import ValidationError as DjangoValidationError

from lasuite.oidc_login.backends import (
    OIDCAuthenticationBackend as LaSuiteOIDCAuthenticationBackend,
)

from core.entitlements import EntitlementsUnavailableError, get_user_entitlements
from core.enums import MailboxRoleChoices, MailDomainAccessRoleChoices
from core.mda.addresses import (
    ascii_lower,
    normalize_address,
    normalize_domain,
    split_address,
)
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

        # Fold before anything keys off it: the stored User.email is the
        # canonical form (see User.clean_fields), so an IdP that varies the
        # casing of a claim between logins must not fork a second account.
        email = normalize_address(user_info.get("email") or "") or None

        claims = {
            self.OIDC_USER_SUB_FIELD: sub,
            "email": email,
        }
        claims.update(**self.get_extra_claims(user_info))

        # if sub is absent, try matching on email
        user = self.get_existing_user(sub, email)

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

        # Resolve domain names to MailDomain objects that exist in DB.
        # MailDomain.name is stored canonicalized, so the entitlement list has
        # to be canonicalized too or an upper-case entry silently grants nothing.
        entitled_domains = list(
            MailDomain.objects.filter(
                name__in=[normalize_domain(str(name)) for name in admin_domains]
            )
        )
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

    def should_create_user(self, email):
        """Check if a user should be created based on the email address."""

        if not email:
            return False

        # With this setting, we always create a user locally
        if self.get_settings("OIDC_CREATE_USER", True):
            return True

        parts = split_address(email)
        if parts is None:
            return False

        # If the email address ends with a domain that has autojoin enabled
        if MailDomain.objects.filter(
            name=normalize_domain(parts[1]), oidc_autojoin=True
        ).exists():
            return True

        # Don't create a user locally
        return False

    def autojoin_mailbox(self, user):
        """Setup autojoin mailbox for user.

        The mailbox is keyed on the folded address, never on the raw claim:
        an IdP that returns ``John.Doe@`` today and ``john.doe@`` tomorrow
        must land on one mailbox, and folding ASCII-only keeps a Unicode
        look-alike local part from resolving onto someone else's.
        """

        # TODO aliases?
        parts = split_address(user.email or "")
        if parts is None:
            return

        local_part = ascii_lower(parts[0])
        domain_name = normalize_domain(parts[1])

        maildomain = MailDomain.objects.filter(
            name=domain_name, oidc_autojoin=True
        ).first()
        if not maildomain:
            return

        # Mailbox local parts are ASCII-only (see Mailbox.local_part's
        # validator). A claim that doesn't fit — a non-ASCII local part, a
        # space — means no mailbox, not a failed login: autojoin is a
        # convenience, and letting the ValidationError escape here would lock
        # the user out of the product entirely.
        try:
            mailbox, _ = Mailbox.objects.get_or_create(
                local_part=local_part,
                domain=maildomain,
            )
        except DjangoValidationError:
            logger.warning(
                "Skipping mailbox autojoin on %s: unsupported local part",
                domain_name,
            )
            return

        # Create an admin mailbox access for the user if needed
        mailbox_access, _ = MailboxAccess.objects.get_or_create(
            mailbox=mailbox,
            user=user,
            defaults={"role": MailboxRoleChoices.ADMIN},
        )
        if mailbox_access.role != MailboxRoleChoices.ADMIN:
            mailbox_access.role = MailboxRoleChoices.ADMIN
            mailbox_access.save()

        # The mailbox's own contact is an address we own, so it carries the
        # folded form rather than whatever casing the IdP happened to send.
        email = str(mailbox)
        contact, _ = Contact.objects.get_or_create(
            email=email,
            mailbox=mailbox,
            defaults={"name": user.full_name or local_part},
        )
        mailbox.contact = contact
        mailbox.save()
        # if not created and contact.mailbox != mailbox:
        #     contact.mailbox = mailbox
        #     contact.save()
