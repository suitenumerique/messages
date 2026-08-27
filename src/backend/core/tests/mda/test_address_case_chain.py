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

from core import factories, models
from core.enums import MessageDeliveryStatusChoices
from core.mda.draft import create_draft
from core.mda.inbound import check_local_recipient, check_local_recipients
from core.mda.outbound import (
    prepare_outbound_message,
    send_message,
    send_outbound_email,
)
from core.tests.mda.test_addresses import UNICODE_ASCII_LOOKALIKES

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

    @pytest.mark.parametrize("lookalike", UNICODE_ASCII_LOOKALIKES)
    def test_lookalike_local_part_does_not_resolve(self, lookalike, settings):
        """``nicK@`` (Kelvin sign) must not be delivered to ``nick@``."""
        settings.MESSAGES_ACCEPT_ALL_EMAILS = False
        domain = factories.MailDomainFactory(name="lookalike.example")
        factories.MailboxFactory(local_part="nick", domain=domain)

        spoofed = f"nic{lookalike}@lookalike.example"
        assert check_local_recipient(spoofed) is False
        assert check_local_recipients([spoofed]) == set()

    def test_batch_check_returns_the_caller_s_own_strings(self, mailbox, settings):
        """MTA-in keys its RCPT verdict by the exact string it sent."""
        settings.MESSAGES_ACCEPT_ALL_EMAILS = False
        addresses = ["John.Doe@EXAMPLE.COM", "nobody@example.com"]
        assert check_local_recipients(addresses) == {"John.Doe@EXAMPLE.COM"}

    def test_batch_check_matches_single_check(self, mailbox, settings):
        settings.MESSAGES_ACCEPT_ALL_EMAILS = False
        addresses = [
            "john.doe@example.com",
            "JOHN.DOE@example.com",
            "john.doe@EXAMPLE.COM",
            "other@example.com",
        ]
        batch = check_local_recipients(addresses)
        for address in addresses:
            assert (address in batch) is bool(check_local_recipient(address))

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

        This is what makes the ``can_split`` branch in ``send_smtp_mail``
        reachable: the message stays ASCII, so a hop without SMTPUTF8 can
        still take the other recipients and only the Bcc address fails.
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
