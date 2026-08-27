"""Address case handling on the OIDC login path.

An IdP is free to vary the casing of the ``email`` claim between logins, and
ProConnect documents the claim as plain UTF-8 with no normalization promise
(it recommends reconciling on ``sub``, which we only fall back from). So the
claim is folded before it keys anything: a second casing must not fork a
second account, and no Unicode look-alike may fold onto an existing one.
"""
# pylint: disable=redefined-outer-name,unused-argument

from django.core.exceptions import ValidationError as DjangoValidationError

import pytest

from core import factories, models
from core.authentication.backends import OIDCAuthenticationBackend
from core.tests.mda.test_addresses import ASCII_FOLD_PAIRS

pytestmark = pytest.mark.django_db


@pytest.fixture
def login(monkeypatch):
    """Return a callable that runs one OIDC login for the given claims."""

    def _login(sub, email):
        monkeypatch.setattr(
            OIDCAuthenticationBackend,
            "get_userinfo",
            lambda *args: {"sub": sub, "email": email},
        )
        return OIDCAuthenticationBackend().get_or_create_user(
            access_token="test-token", id_token=None, payload=None
        )

    return _login


class TestUserIdentityFolding:
    """``User.email`` is stored and matched in canonical form."""

    def test_email_is_folded_on_save(self):
        user = factories.UserFactory(email="John.DOE@EXAMPLE.COM")
        assert user.email == "john.doe@example.com"

    def test_mixed_case_claim_matches_the_existing_user(self, login):
        existing = factories.UserFactory(email="john.doe@example.com", sub=None)

        user = login(sub="new-sub", email="John.Doe@Example.com")

        assert user.pk == existing.pk
        assert models.User.objects.filter(email="john.doe@example.com").count() == 1

    def test_two_casings_do_not_fork_two_accounts(self, login, settings):
        settings.OIDC_CREATE_USER = True
        first = login(sub="sub-1", email="jane@example.com")
        second = login(sub="sub-1", email="JANE@EXAMPLE.COM")
        assert first.pk == second.pk
        assert models.User.objects.filter(email="jane@example.com").count() == 1

    @pytest.mark.parametrize(("lookalike", "ascii_char"), ASCII_FOLD_PAIRS)
    def test_lookalike_claim_never_reaches_an_existing_account(
        self, login, lookalike, ascii_char, settings
    ):
        """The CVE-2019-19844 shape, asserted as an invariant.

        Some look-alikes get their own account, others are refused by
        Django's EmailField before that. Either outcome is acceptable;
        resolving onto the victim's account is not.
        """
        settings.OIDC_CREATE_USER = True
        settings.OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION = True
        victim = factories.UserFactory(email=f"nic{ascii_char}@example.com", sub=None)

        try:
            attacker = login(sub="attacker-sub", email=f"nic{lookalike}@example.com")
        except DjangoValidationError:
            attacker = None

        assert attacker is None or attacker.pk != victim.pk
        victim.refresh_from_db()
        assert victim.sub is None


class TestAutojoinMailbox:
    """Autojoin resolves the mailbox from the folded claim."""

    @pytest.fixture
    def domain(self):
        return factories.MailDomainFactory(name="example.com", oidc_autojoin=True)

    def test_mailbox_is_created_folded(self, login, domain):
        user = login(sub="sub-1", email="John.Doe@Example.com")

        mailbox = models.Mailbox.objects.get(domain=domain)
        assert mailbox.local_part == "john.doe"
        assert mailbox.accesses.get().user == user

    def test_second_casing_reuses_the_same_mailbox(self, login, domain):
        login(sub="sub-1", email="john.doe@example.com")
        login(sub="sub-1", email="JOHN.DOE@EXAMPLE.COM")

        assert models.Mailbox.objects.filter(domain=domain).count() == 1

    def test_mailbox_contact_carries_the_folded_address(self, login, domain):
        """The mailbox's own contact becomes its From header; keep it canonical."""
        login(sub="sub-1", email="John.Doe@Example.com")

        mailbox = models.Mailbox.objects.get(domain=domain)
        assert mailbox.contact.email == "john.doe@example.com"

    def test_kelvin_sign_claim_logs_in_without_joining_the_mailbox(self, login, domain):
        """``nicK@`` (U+212A) must not reach ``nick@`` — and must not 500.

        This is the sharpest case: Django's EmailField accepts the Kelvin
        sign (its local-part regex is case-insensitive, and U+212A folds to
        "k"), so the User row is created and autojoin does run. Only the
        ASCII-only fold keeps it off the victim's mailbox, and only the
        autojoin guard keeps the ValidationError from failing the login.
        """
        victim = factories.MailboxFactory(local_part="nick", domain=domain)

        user = login(sub="attacker-sub", email="nicK@example.com")

        assert user is not None
        assert not victim.accesses.exists()
        assert models.Mailbox.objects.filter(domain=domain).count() == 1

    @pytest.mark.parametrize(("lookalike", "ascii_char"), ASCII_FOLD_PAIRS)
    def test_lookalike_claim_never_joins_an_existing_mailbox(
        self, login, domain, lookalike, ascii_char
    ):
        """Whatever happens to the attacker, the victim's mailbox is untouched."""
        victim = factories.MailboxFactory(local_part=f"nic{ascii_char}", domain=domain)

        try:
            login(sub="attacker-sub", email=f"nic{lookalike}@example.com")
        except DjangoValidationError:
            pass

        assert not victim.accesses.exists()
        assert models.Mailbox.objects.filter(domain=domain).count() == 1

    def test_claim_without_an_at_sign_is_ignored(self, login, domain):
        login(sub="sub-1", email="not-an-email")
        assert not models.Mailbox.objects.filter(domain=domain).exists()

    def test_no_mailbox_when_autojoin_is_off(self, login):
        factories.MailDomainFactory(name="nojoin.example", oidc_autojoin=False)
        login(sub="sub-1", email="john.doe@NOJOIN.example")
        assert not models.Mailbox.objects.filter(domain__name="nojoin.example").exists()


class TestAdminLoginFolding:
    """The local admin login resolves through the same canonical form."""

    def test_admin_email_is_folded_on_save(self):
        user = factories.UserFactory(admin_email="Admin@EXAMPLE.COM")
        assert user.admin_email == "admin@example.com"

    def test_lookup_accepts_the_casing_the_admin_typed(self):
        """get_by_natural_key is the single funnel Django's auth backend uses."""
        user = factories.UserFactory(admin_email="admin@example.com")

        assert models.User.objects.get_by_natural_key("Admin@Example.COM") == user
        assert models.User.objects.get_by_natural_key("admin@example.com") == user

    def test_lookup_still_misses_a_different_address(self):
        factories.UserFactory(admin_email="admin@example.com")

        with pytest.raises(models.User.DoesNotExist):
            models.User.objects.get_by_natural_key("someone@example.com")

    @pytest.mark.parametrize(("lookalike", "ascii_char"), ASCII_FOLD_PAIRS)
    def test_lookup_rejects_a_lookalike_local_part(self, lookalike, ascii_char):
        """An admin credential must not be reachable by a look-alike spelling.

        The local part is where this matters, and where folding is ASCII-only.
        A look-alike in the *domain* is a different question: UTS-46 maps some
        of those onto ASCII by design, because that is what DNS itself does
        (see ``TestNormalizeDomain.test_uts46_maps_fullwidth_to_ascii``).
        """
        victim = f"{ascii_char}dmin@example.com"
        factories.UserFactory(admin_email=victim)

        # Sanity: the ASCII form does resolve, so the negative below is real.
        assert models.User.objects.get_by_natural_key(victim) is not None
        with pytest.raises(models.User.DoesNotExist):
            models.User.objects.get_by_natural_key(f"{lookalike}dmin@example.com")
