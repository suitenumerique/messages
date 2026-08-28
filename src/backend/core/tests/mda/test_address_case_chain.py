"""End-to-end case / Unicode handling for addresses.

Covers the split policy stated in :mod:`core.mda.addresses`: addresses we
*own* are folded (mailboxes, login, inbound recipients), addresses we merely
*carry* keep their case (contacts, MIME headers), and nothing outside ASCII
may ever fold onto an address we own.
"""
# pylint: disable=redefined-outer-name,unused-argument

from django.core.exceptions import ValidationError

import pytest
import rest_framework as drf
from rest_framework.test import APIClient

from core import factories, models
from core.enums import MessageDeliveryStatusChoices
from core.mda.draft import create_draft, update_draft
from core.mda.inbound import check_local_recipient, check_local_recipients
from core.mda.inbound_tasks import _is_selfcheck
from core.mda.outbound import (
    prepare_outbound_message,
    send_message,
    send_outbound_email,
)
from core.mda.outbound_direct import group_recipients_by_mx
from core.tests.mda.test_addresses import (
    ASCII_FOLD_PAIRS,
    KELVIN_SIGN,
    UNICODE_ASCII_LOOKALIKES,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def domain():
    """A mail domain with one lowercase mailbox on it."""
    return factories.MailDomainFactory(name="example.com")


@pytest.fixture
def mailbox(domain):
    """``john.doe@example.com``."""
    return factories.MailboxFactory(local_part="john.doe", domain=domain)


class TestMailboxFolding:
    """Mailbox local parts are stored folded and collide case-insensitively."""

    def test_local_part_is_folded_on_save(self, domain):
        mailbox = factories.MailboxFactory(local_part="John.DOE", domain=domain)
        assert mailbox.local_part == "john.doe"
        assert str(mailbox) == "john.doe@example.com"

    def test_case_variant_cannot_coexist(self, mailbox, domain):
        """The unique_together check runs on the folded value."""
        with pytest.raises(ValidationError):
            factories.MailboxFactory(local_part="John.Doe", domain=domain)

    @pytest.mark.parametrize("lookalike", UNICODE_ASCII_LOOKALIKES)
    def test_unicode_local_part_is_rejected_outright(self, lookalike, domain):
        """We are ASCII-only, so a look-alike never even becomes a mailbox."""
        with pytest.raises(ValidationError):
            factories.MailboxFactory(local_part=f"nic{lookalike}", domain=domain)


class TestInboundRecipientResolution:
    """Inbound delivery resolves recipients case-insensitively."""

    @pytest.mark.parametrize(
        "address",
        [
            "john.doe@example.com",
            "John.Doe@example.com",
            "JOHN.DOE@EXAMPLE.COM",
            "john.doe@Example.Com",
        ],
    )
    def test_resolves_any_case(self, mailbox, address, settings):
        settings.MESSAGES_ACCEPT_ALL_EMAILS = False
        assert check_local_recipient(address) is True

    def test_resolved_mailbox_is_the_same_row(self, mailbox, settings):
        settings.MESSAGES_ACCEPT_ALL_EMAILS = False
        resolved = check_local_recipient("JOHN.DOE@EXAMPLE.COM", create_if_missing=True)
        assert resolved.pk == mailbox.pk
        assert models.Mailbox.objects.filter(domain=mailbox.domain).count() == 1

    def test_created_mailbox_is_folded(self, domain, settings):
        """MESSAGES_ACCEPT_ALL_EMAILS creates on demand; it must create folded."""
        settings.MESSAGES_ACCEPT_ALL_EMAILS = True
        created = check_local_recipient("New.User@EXAMPLE.COM", create_if_missing=True)
        assert created.local_part == "new.user"
        assert created.domain.name == "example.com"

    @pytest.mark.parametrize(("lookalike", "ascii_char"), ASCII_FOLD_PAIRS)
    def test_lookalike_local_part_does_not_resolve(
        self, lookalike, ascii_char, settings
    ):
        """A homoglyph must not be delivered to the address it folds onto.

        The victim is built FROM the pair, so every case is a real collision:
        parametrizing over look-alikes that fold onto some other letter would
        pass against correct and broken code alike.
        """
        settings.MESSAGES_ACCEPT_ALL_EMAILS = False
        domain = factories.MailDomainFactory(name="lookalike.example")
        victim = f"nic{ascii_char}"
        factories.MailboxFactory(local_part=victim, domain=domain)

        spoofed = f"nic{lookalike}@lookalike.example"
        assert check_local_recipient(f"{victim}@lookalike.example") is True
        assert check_local_recipient(spoofed) is False
        assert check_local_recipients([spoofed]) == set()

    def test_batch_check_returns_the_caller_s_own_strings(self, mailbox, settings):
        """MTA-in keys its RCPT verdict by the exact string it sent."""
        settings.MESSAGES_ACCEPT_ALL_EMAILS = False
        addresses = ["John.Doe@EXAMPLE.COM", "nobody@example.com"]
        assert check_local_recipients(addresses) == {"John.Doe@EXAMPLE.COM"}

    @pytest.mark.parametrize("accept_all", [False, True])
    def test_batch_check_matches_single_check(self, mailbox, settings, accept_all):
        """One invariant, under both settings and every address shape.

        The batch answers RCPT TO and the single one answers DATA, so an
        address they disagree on is accepted at RCPT and then fails at DATA —
        which MTA-in maps to a 451, so the sender retries the whole envelope
        for its full backoff window rather than learning at RCPT.

        Both axes matter: pinning only ``accept_all=False`` leaves the branch
        that skips the mailbox lookup entirely unchecked.
        """
        settings.MESSAGES_ACCEPT_ALL_EMAILS = accept_all
        factories.MailDomainFactory(name="exemplé.example")
        addresses = [
            "john.doe@example.com",
            "JOHN.DOE@example.com",
            "john.doe@EXAMPLE.COM",
            "other@example.com",
            "josé@example.com",  # no mailbox can carry this local part
            "user@exemplé.example",  # IDN domain, U-label as MTA-in sends it
            "nodomain",  # malformed
        ]
        batch = check_local_recipients(addresses)
        for address in addresses:
            assert (address in batch) is bool(check_local_recipient(address)), address

    def test_eai_address_is_never_local_even_when_accepting_all(self, settings):
        """We host no EAI mailboxes, so such an address must never resolve.

        Under MESSAGES_ACCEPT_ALL_EMAILS the create-if-missing path would
        otherwise raise ValidationError straight out of ``send_message``,
        which does not guard this call, leaving the message retrying forever.
        """
        settings.MESSAGES_ACCEPT_ALL_EMAILS = True

        assert check_local_recipient("josé@example.com") is False
        assert check_local_recipient("josé@example.com", create_if_missing=True) is (
            False
        )
        assert not models.Mailbox.objects.filter(local_part="josé").exists()

    def test_idn_domain_resolves_from_its_u_label(self, settings):
        """The DB holds the A-label; a U-label RCPT must still land."""
        settings.MESSAGES_ACCEPT_ALL_EMAILS = False
        idn_domain = factories.MailDomainFactory(name="exemplé.example")
        assert idn_domain.name == "xn--exempl-gva.example"
        factories.MailboxFactory(local_part="user", domain=idn_domain)

        assert check_local_recipient("user@exemplé.example") is True
        assert check_local_recipient("user@xn--exempl-gva.example") is True


class TestOutboundEnvelope:
    """The SMTP envelope carries an ASCII wire form, or fails that recipient."""

    RELAY_SETTINGS = {"MTA_OUT_MODE": "relay", "MTA_OUT_RELAY_HOST": "relay.test:25"}

    def test_idn_domain_is_a_labelled_on_the_wire(self, monkeypatch):
        captured = {}

        def fake_send_smtp_mail(**kwargs):
            captured.update(kwargs)
            return {email: {"delivered": True} for email in kwargs["recipient_emails"]}

        monkeypatch.setattr("core.mda.outbound.send_smtp_mail", fake_send_smtp_mail)

        statuses = send_outbound_email(
            {"Someone@Exemplé.example"},
            "sender@example.com",
            b"raw",
            self.RELAY_SETTINGS,
        )

        # Local part case survives, domain does not.
        assert captured["recipient_emails"] == {"Someone@xn--exempl-gva.example"}
        # ...and the caller gets its own key back, which is what the
        # MessageRecipient rows are keyed on.
        assert statuses == {"Someone@Exemplé.example": {"delivered": True}}

    def test_non_ascii_local_part_reaches_the_smtp_layer(self, monkeypatch):
        """We attempt delivery now; the hop decides, not us.

        Whether it can actually be sent is a per-hop SMTPUTF8 question,
        answered in ``send_smtp_mail`` where the EHLO response is known.
        """
        captured = {}

        def fake_send_smtp_mail(**kwargs):
            captured.update(kwargs)
            return {email: {"delivered": True} for email in kwargs["recipient_emails"]}

        monkeypatch.setattr("core.mda.outbound.send_smtp_mail", fake_send_smtp_mail)

        statuses = send_outbound_email(
            {"josé@example.com", "ok@example.com"},
            "sender@example.com",
            b"raw",
            self.RELAY_SETTINGS,
        )

        assert captured["recipient_emails"] == {"josé@example.com", "ok@example.com"}
        assert statuses["josé@example.com"]["delivered"] is True

    def test_two_casings_of_one_address_both_get_the_status(self, monkeypatch):
        """Distinct recipients can share one wire form; neither may lose its status.

        A recipient with no status reads as "outcome unknown" to
        ``send_message``, which retries it — re-sending mail the MTA already
        accepted.
        """
        captured = {}

        def fake_send_smtp_mail(**kwargs):
            captured.update(kwargs)
            return {email: {"delivered": True} for email in kwargs["recipient_emails"]}

        monkeypatch.setattr("core.mda.outbound.send_smtp_mail", fake_send_smtp_mail)

        statuses = send_outbound_email(
            {"user@Example.com", "user@example.com"},
            "sender@example.com",
            b"raw",
            self.RELAY_SETTINGS,
        )

        # One RCPT on the wire, two recipients accounted for.
        assert captured["recipient_emails"] == {"user@example.com"}
        assert statuses["user@Example.com"]["delivered"] is True
        assert statuses["user@example.com"]["delivered"] is True

    def test_domain_with_no_a_label_fails_before_smtp(self, monkeypatch):
        """Still no wire form at all, so it never reaches a connection."""

        def explode(**_kwargs):
            raise AssertionError("SMTP must not be opened with nothing to send")

        monkeypatch.setattr("core.mda.outbound.send_smtp_mail", explode)

        statuses = send_outbound_email(
            {"user@a..é"}, "sender@example.com", b"raw", self.RELAY_SETTINGS
        )
        assert statuses["user@a..é"]["delivered"] is False
        assert statuses["user@a..é"]["retry"] is False


class TestComposedHeadersKeepTheirCase:
    """What the user typed is what the recipient sees."""

    def _compose(self, mailbox, recipient_email):
        """Build and DKIM-sign the MIME for a one-recipient draft."""
        message = create_draft(
            mailbox=mailbox, subject="Case test", to_emails=[recipient_email]
        )
        assert prepare_outbound_message(mailbox, message, "Hello.", "<p>Hello.</p>")
        message.refresh_from_db()
        return message.blob.get_content().decode("utf-8", "replace")

    def test_recipient_case_is_preserved_in_the_to_header(self, mailbox):
        """Only the destination host may fold a local part, so we do not."""
        mime = self._compose(mailbox, "John.Doe@Other.Example")
        assert "John.Doe@Other.Example" in mime

    def test_idn_recipient_domain_is_a_labelled_in_the_to_header(self, mailbox):
        """7-bit SMTP carries no U-label; the composer emits the A-label."""
        mime = self._compose(mailbox, "Someone@exemplé.example")
        assert "Someone@xn--exempl-gva.example" in mime
        assert "exemplé" not in mime

    def test_eai_in_bcc_keeps_the_headers_ascii(self, mailbox):
        """Bcc never reaches the header block, so it cannot force RFC 6532.

        The message stays ASCII, which matters for every *other* hop: an
        ASCII recipient on a different MX is still handed a plain message.
        Within one transaction an accented Bcc address does block its
        co-recipients, since we do not split the transaction.
        """
        message = create_draft(
            mailbox=mailbox,
            subject="Bcc test",
            to_emails=["ok@other.example"],
            bcc_emails=["josé@other.example"],
        )
        assert prepare_outbound_message(mailbox, message, "Hello.", "<p>Hello.</p>")
        message.refresh_from_db()
        mime = message.blob.get_content()

        assert mime.isascii()
        assert b"jos" not in mime

    def test_eai_in_to_makes_the_whole_message_utf8(self, mailbox):
        """The contrast: in To/Cc it does force RFC 6532 for everyone."""
        message = create_draft(
            mailbox=mailbox,
            subject="To test",
            to_emails=["josé@other.example", "ok@other.example"],
        )
        assert prepare_outbound_message(mailbox, message, "Hello.", "<p>Hello.</p>")
        message.refresh_from_db()

        assert not message.blob.get_content().isascii()

    def test_non_ascii_local_part_composes_with_smtputf8(self, mailbox):
        """We attempt EAI delivery, so the address must survive composition.

        RFC 6532 headers are UTF-8, so the address appears as itself rather
        than being encoded or dropped.
        """
        mime = self._compose(mailbox, "josé@other.example")
        assert "josé@other.example" in mime

    def test_a_bad_address_does_not_wipe_the_existing_recipients(self, mailbox):
        """Validation runs before anything is deleted.

        ``update_draft`` clears the recipients of a type before recreating
        them, so a mid-loop raise would leave the draft half wiped while the
        caller sees only the 400.
        """
        message = create_draft(
            mailbox=mailbox, subject="Keep", to_emails=["first@other.example"]
        )
        assert message.recipients.count() == 1

        with pytest.raises(drf.exceptions.ValidationError):
            update_draft(
                mailbox,
                message,
                {"to": ["ok@other.example", "two@example.com, addresses@example.com"]},
            )

        # Untouched: still the original recipient, not zero and not a partial set.
        assert [r.contact.email for r in message.recipients.all()] == [
            "first@other.example"
        ]

    def test_malformed_recipient_is_a_bad_request_not_a_500(self, mailbox):
        """A value that is not one addr-spec is the client's error.

        Left uncaught, the Django ValidationError from Contact.email escapes
        DRF's handler as a 500 while the user is simply typing a recipient.
        """
        with pytest.raises(drf.exceptions.ValidationError):
            create_draft(
                mailbox=mailbox,
                subject="Case test",
                to_emails=["two@example.com, addresses@example.com"],
            )


class TestDeliveryFailureReachesTheUser:
    """The SMTPUTF8 refusal has to survive all the way to the recipient row.

    ``MessageRecipient.delivery_message`` is what the contact popover shows
    under its "Show logs" toggle (``En savoir plus`` in French), so this is
    the string the user actually reads when an accented address bounces.
    """

    def test_smtputf8_refusal_is_stored_on_the_recipient(self, mailbox, monkeypatch):
        message = create_draft(
            mailbox=mailbox, subject="EAI", to_emails=["josé@other.example"]
        )
        assert prepare_outbound_message(mailbox, message, "Hello.", "<p>Hello.</p>")
        message.refresh_from_db()

        refusal = (
            "The receiving mail server (mx.other.example) does not support "
            "internationalized email addresses (SMTPUTF8, RFC 6531), which is "
            "required to deliver this message"
        )
        monkeypatch.setattr(
            "core.mda.outbound.send_outbound_message",
            lambda recipients, _message, _mime: {
                email: {"delivered": False, "error": refusal, "retry": False}
                for email in recipients
            },
        )
        send_message(message)

        recipient = message.recipients.get()
        assert recipient.delivery_status == MessageDeliveryStatusChoices.FAILED
        assert recipient.delivery_message == refusal
        # Not queued for another attempt: the extension will not appear later.
        assert recipient.retry_at is None


class TestInternalDelivery:
    """The same-instance fast path never opens an SMTP session.

    It therefore skips every SMTPUTF8 negotiation and wire-form conversion
    in ``send_outbound_email``/``send_smtp_mail``, so the folding it relies
    on has to be correct on its own.
    """

    def test_mixed_case_recipient_lands_in_the_folded_mailbox(
        self, mailbox, domain, settings
    ):
        """Routing and delivery both fold, so no second mailbox is conjured."""
        settings.MESSAGES_ACCEPT_ALL_EMAILS = False
        recipient = factories.MailboxFactory(local_part="jane.roe", domain=domain)

        message = create_draft(
            mailbox=mailbox, subject="Internal", to_emails=["Jane.ROE@Example.com"]
        )
        assert prepare_outbound_message(mailbox, message, "Hi.", "<p>Hi.</p>")
        message.refresh_from_db()
        send_message(message)

        assert message.recipients.get().delivery_status == (
            MessageDeliveryStatusChoices.SENT_INTERNAL
        )
        # The sender's own mailbox plus the recipient's, and nothing else.
        assert models.Mailbox.objects.filter(domain=domain).count() == 2
        assert models.Message.objects.filter(
            thread__accesses__mailbox=recipient, is_sender=False
        ).exists()

    def test_eai_recipient_never_takes_the_internal_path(
        self, mailbox, settings, monkeypatch
    ):
        """MESSAGES_ACCEPT_ALL_EMAILS makes everything look local; EAI must not be.

        Otherwise the internal path would try to create a mailbox with a
        non-ASCII local part, which we do not host.
        """
        settings.MESSAGES_ACCEPT_ALL_EMAILS = True

        def no_internal(*_args, **_kwargs):
            raise AssertionError("EAI must not be delivered internally")

        monkeypatch.setattr("core.mda.outbound.deliver_inbound_message", no_internal)
        sent = {}
        monkeypatch.setattr(
            "core.mda.outbound.send_outbound_message",
            lambda recipients, _m, _d: (
                sent.update(recipients=set(recipients))
                or {email: {"delivered": True} for email in recipients}
            ),
        )

        message = create_draft(
            mailbox=mailbox, subject="EAI", to_emails=["josé@other.example"]
        )
        assert prepare_outbound_message(mailbox, message, "Hi.", "<p>Hi.</p>")
        message.refresh_from_db()
        send_message(message)

        assert sent["recipients"] == {"josé@other.example"}
        assert message.recipients.get().delivery_status == (
            MessageDeliveryStatusChoices.SENT_EXTERNAL
        )

    def test_utf8_headers_survive_the_internal_handoff(
        self, mailbox, domain, settings, monkeypatch
    ):
        """A mixed message: one EAI recipient out, one local recipient inside.

        The single stored blob carries RFC 6532 headers, and the internal
        path hands those exact bytes to the recipient's inbound pipeline.
        The local recipient must still end up with a readable message.
        """
        settings.MESSAGES_ACCEPT_ALL_EMAILS = False
        recipient = factories.MailboxFactory(local_part="jane.roe", domain=domain)
        monkeypatch.setattr(
            "core.mda.outbound.send_outbound_message",
            lambda recipients, _m, _d: {
                email: {"delivered": True} for email in recipients
            },
        )

        message = create_draft(
            mailbox=mailbox,
            subject="Mixed",
            to_emails=["josé@other.example", "jane.roe@example.com"],
        )
        assert prepare_outbound_message(mailbox, message, "Hi.", "<p>Hi.</p>")
        message.refresh_from_db()
        # The headers really are 8-bit, otherwise this proves nothing.
        assert not message.blob.get_content().isascii()

        send_message(message)

        delivered = models.Message.objects.filter(
            thread__accesses__mailbox=recipient, is_sender=False
        ).first()
        assert delivered is not None
        parsed = delivered.get_parsed_data()
        assert parsed is not None
        assert "josé@other.example" in str(parsed.get("to"))


class TestFoldingOnSecurityGates:
    """Comparisons that gate something must fold ASCII-only.

    Reachable: pymta advertises SMTPUTF8, so a homoglyph really can arrive.
    The submit endpoint's From check is covered in ``test_submit.py``.
    """

    def test_selfcheck_gate_rejects_a_homoglyph(self, settings):
        """The self-probe gate skips spam checking, so it must not fold.

        The homoglyph goes in the *local part*: that is the half folded
        ASCII-only. A homoglyph in the domain is a different question, since
        UTS-46 maps some of those onto ASCII by design (they really are the
        same DNS name) — see ``TestNormalizeDomain``.
        """
        settings.MESSAGES_SELFCHECK_FROM = "probek@example.com"
        settings.MESSAGES_SELFCHECK_TO = "sink@example.com"

        spoofed = {"from": [{"email": f"probe{KELVIN_SIGN}@example.com"}]}
        assert _is_selfcheck(spoofed, "sink@example.com") is False

        # The real probe still matches, whatever case it arrives in.
        genuine = {"from": [{"email": "ProbeK@Example.com"}]}
        assert _is_selfcheck(genuine, "SINK@example.com") is True


class TestMxGrouping:
    """Every recipient handed to MX grouping must come back out."""

    def test_quoted_local_part_is_not_dropped(self):
        """A quoted local part may contain @, and Contact.email now allows it.

        Dropping it here yields no delivery status, which ``send_message``
        reads as "outcome unknown" and retries for the whole backoff window.
        """
        grouped = group_recipients_by_mx(['"a@b"@example.com', "plain@example.com"])

        offered = {email for group in grouped.values() for email in group["recipients"]}
        assert offered == {'"a@b"@example.com', "plain@example.com"}

    def test_domain_is_canonicalized_for_grouping(self):
        """Case variants of one domain must share a single MX lookup."""
        grouped = group_recipients_by_mx(["a@Example.COM", "b@example.com"])

        assert list(grouped) == ["example.com"]
        assert len(grouped["example.com"]["recipients"]) == 2

    def test_malformed_is_still_dropped(self):
        assert group_recipients_by_mx(["nodomain", "user@"]) == {}


@pytest.mark.django_db
class TestDraftApiAcceptsEai:
    """The documented contract and the code must agree.

    The draft endpoint's schema block is documentation only (the view reads
    ``request.data``), so an ``EmailField`` there published a narrower
    contract than the endpoint actually honours. This asserts the real one.
    """

    def test_accented_recipient_round_trips_through_the_api(self, mailbox):
        user = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.EDITOR
        )
        client = APIClient()
        client.force_login(user)

        response = client.post(
            "/api/v1.0/draft/",
            {
                "senderId": str(mailbox.id),
                "subject": "EAI",
                "draftBody": "hi",
                "to": ["josé@other.example"],
            },
            format="json",
        )

        assert response.status_code == 201, response.content
        message = models.Message.objects.get(id=response.json()["id"])
        assert [r.contact.email for r in message.recipients.all()] == [
            "josé@other.example"
        ]
