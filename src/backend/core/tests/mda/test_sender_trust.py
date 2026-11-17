"""Tests for the sender trust backends and the ``sender_trust`` pipeline step."""

from unittest.mock import Mock, patch

from django.test import override_settings
from django.utils import timezone

import pytest

from core import enums, factories
from core.mda.inbound_pipeline import (
    Decision,
    InboundContext,
    build_inbound_pipeline,
    run_inbound_pipeline,
)
from core.mda.sender_trust.backends.base import (
    SenderTrust,
    SenderTrustUnavailableError,
)
from core.mda.sender_trust.backends.local import LocalSenderTrustBackend
from core.mda.sender_trust.backends.null import NullSenderTrustBackend
from core.mda.sender_trust.factory import get_sender_trust_backend

SENDER = "friend@partner.example"
NULL_BACKEND = "core.mda.sender_trust.backends.null.NullSenderTrustBackend"


@pytest.fixture(autouse=True)
def _fresh_backend():
    """The factory caches its instance: reset it around each test."""
    get_sender_trust_backend.cache_clear()
    yield
    get_sender_trust_backend.cache_clear()


def _write_to(
    mailbox,
    email,
    recipient_type=enums.MessageRecipientTypeChoices.TO,
    is_draft=False,
):
    """Record a message sent by ``mailbox`` with ``email`` as a recipient."""
    message = factories.MessageFactory(
        sender=factories.ContactFactory(mailbox=mailbox, email=str(mailbox)),
        is_sender=True,
        is_draft=is_draft,
    )
    factories.MessageRecipientFactory(
        message=message,
        contact=factories.ContactFactory(mailbox=mailbox, email=email),
        type=recipient_type,
    )


@pytest.mark.django_db
class TestLocalSenderTrustBackend:
    """A sender is trusted once the mailbox has written to them."""

    @pytest.mark.parametrize(
        "recipient_type",
        [
            enums.MessageRecipientTypeChoices.TO,
            enums.MessageRecipientTypeChoices.CC,
            enums.MessageRecipientTypeChoices.BCC,
        ],
    )
    def test_trusted_after_writing_to_sender(self, recipient_type):
        mailbox = factories.MailboxFactory()
        _write_to(mailbox, SENDER, recipient_type=recipient_type)

        trust = LocalSenderTrustBackend().get_sender_trust(mailbox, SENDER)

        assert trust == SenderTrust.TRUSTED

    def test_unknown_when_only_received_from_sender(self):
        """Receiving creates a Contact, which must not be enough."""
        mailbox = factories.MailboxFactory()
        received = factories.MessageFactory(
            sender=factories.ContactFactory(mailbox=mailbox, email=SENDER),
            is_sender=False,
        )
        # The sender also Cc'd themselves on the received message.
        factories.MessageRecipientFactory(
            message=received,
            contact=factories.ContactFactory(mailbox=mailbox, email=SENDER),
            type=enums.MessageRecipientTypeChoices.CC,
        )

        trust = LocalSenderTrustBackend().get_sender_trust(mailbox, SENDER)

        assert trust == SenderTrust.UNKNOWN

    def test_unknown_for_unsent_draft(self):
        mailbox = factories.MailboxFactory()
        _write_to(mailbox, SENDER, is_draft=True)

        trust = LocalSenderTrustBackend().get_sender_trust(mailbox, SENDER)

        assert trust == SenderTrust.UNKNOWN

    def test_unknown_when_another_mailbox_wrote_to_sender(self):
        mailbox = factories.MailboxFactory()
        _write_to(factories.MailboxFactory(), SENDER)

        trust = LocalSenderTrustBackend().get_sender_trust(mailbox, SENDER)

        assert trust == SenderTrust.UNKNOWN

    def test_contact_casing_is_ignored(self):
        """``Contact.email`` keeps the casing it was typed with."""
        mailbox = factories.MailboxFactory()
        _write_to(mailbox, "Friend@Partner.Example")

        trust = LocalSenderTrustBackend().get_sender_trust(mailbox, SENDER)

        assert trust == SenderTrust.TRUSTED


@pytest.mark.django_db
class TestSenderTrustFactory:
    """The backend is picked by ``SENDER_TRUST_BACKEND``."""

    def test_local_backend_by_default(self):
        assert isinstance(get_sender_trust_backend(), LocalSenderTrustBackend)

    @override_settings(SENDER_TRUST_BACKEND=NULL_BACKEND)
    def test_null_backend_trusts_nobody(self):
        mailbox = factories.MailboxFactory()
        _write_to(mailbox, SENDER)

        backend = get_sender_trust_backend()

        assert isinstance(backend, NullSenderTrustBackend)
        assert backend.get_sender_trust(mailbox, SENDER) == SenderTrust.UNKNOWN


@pytest.mark.django_db
class TestSenderTrustPipeline:
    """The ``sender_trust`` step keeps a known correspondent out of Junk."""

    SPAM_CONFIG = {"rspamd_url": "http://rspamd:11334", "inbound_auth": "native"}

    def _ctx(self, mailbox, spam_config=None, from_addresses=None):
        if from_addresses is None:
            from_addresses = [{"email": SENDER, "name": "Friend"}]
        return InboundContext(
            mailbox=mailbox,
            inbound_message=Mock(
                id="i1", created_at=timezone.now(), is_internal=False, envelope={}
            ),
            recipient_email=str(mailbox),
            raw_data=b"raw",
            parsed_email={"from": from_addresses},
            spam_config=self.SPAM_CONFIG if spam_config is None else spam_config,
        )

    def _run(self, ctx, action, auth_verdict=None):
        """Run the real pipeline with rspamd answering ``action``."""
        with (
            patch(
                "core.mda.spam.call_rspamd",
                return_value=(action, None, {"action": action}),
            ) as mock_rspamd,
            patch(
                "core.mda.inbound_pipeline.check_inbound_authentication",
                return_value=auth_verdict,
            ),
        ):
            decision, step = run_inbound_pipeline(build_inbound_pipeline(ctx), ctx)
        return decision, step, mock_rspamd

    def test_step_order(self):
        ctx = self._ctx(factories.MailboxFactory())

        names = [step.name for step in build_inbound_pipeline(ctx)]

        assert names == [
            "arc",
            "hardcoded_rules",
            "inbound_auth",
            "sender_trust",
            "rspamd",
        ]

    @pytest.mark.parametrize("action", ["reject", "quarantine"])
    def test_trusted_sender_kept_out_of_junk(self, action):
        mailbox = factories.MailboxFactory()
        _write_to(mailbox, SENDER)
        ctx = self._ctx(mailbox)

        decision, _step, _rspamd = self._run(ctx, action)

        assert decision == Decision.CONTINUE
        assert ctx.sender_trust == SenderTrust.TRUSTED
        assert ctx.is_spam is False
        assert ctx.postmark["spam"] == "likely"

    def test_unknown_sender_goes_to_junk(self):
        ctx = self._ctx(factories.MailboxFactory())

        decision, _step, _rspamd = self._run(ctx, "reject")

        assert decision == Decision.CONTINUE
        assert ctx.sender_trust == SenderTrust.UNKNOWN
        assert ctx.is_spam is True
        assert "spam" not in ctx.postmark

    @pytest.mark.parametrize(
        "action,decision,is_spam,marker",
        [
            ("no action", Decision.CONTINUE, False, None),
            ("add header", Decision.CONTINUE, False, "possible"),
            ("discard", Decision.DROP, None, None),
            ("greylist", Decision.RETRY, None, None),
            ("soft reject", Decision.RETRY, None, None),
        ],
    )
    def test_trust_only_lifts_the_junk_verdict(self, action, decision, is_spam, marker):
        """Every other rspamd action (antivirus discard included) still applies."""
        mailbox = factories.MailboxFactory()
        _write_to(mailbox, SENDER)
        ctx = self._ctx(mailbox)

        result, _step, _rspamd = self._run(ctx, action)

        assert result == decision
        assert ctx.is_spam is is_spam
        assert ctx.postmark.get("spam") == marker

    @pytest.mark.parametrize(
        "spam_config,auth_verdict",
        [
            # inbound_auth disabled records no verdict, like a verified sender.
            ({"rspamd_url": "http://rspamd:11334"}, None),
            (SPAM_CONFIG, "none"),
            (SPAM_CONFIG, "fail"),
        ],
    )
    def test_unauthenticated_sender_not_looked_up(self, spam_config, auth_verdict):
        mailbox = factories.MailboxFactory()
        _write_to(mailbox, SENDER)
        ctx = self._ctx(mailbox, spam_config=spam_config)

        with patch(
            "core.mda.inbound_pipeline.get_sender_trust_backend"
        ) as mock_factory:
            self._run(ctx, "reject", auth_verdict=auth_verdict)

        mock_factory.assert_not_called()
        assert ctx.sender_trust is None
        assert ctx.is_spam is True

    def test_decided_verdict_not_looked_up(self):
        """An admin rule, a webhook or internal mail already decided."""
        mailbox = factories.MailboxFactory()
        _write_to(mailbox, SENDER)
        ctx = self._ctx(mailbox)
        ctx.is_spam = True

        with patch(
            "core.mda.inbound_pipeline.get_sender_trust_backend"
        ) as mock_factory:
            _decision, _step, mock_rspamd = self._run(ctx, "no action")

        mock_factory.assert_not_called()
        mock_rspamd.assert_not_called()
        assert ctx.is_spam is True

    @pytest.mark.parametrize(
        "from_addresses",
        [
            [],
            [{"email": SENDER}, {"email": "other@partner.example"}],
        ],
    )
    def test_no_single_from_not_looked_up(self, from_addresses):
        mailbox = factories.MailboxFactory()
        _write_to(mailbox, SENDER)
        ctx = self._ctx(mailbox, from_addresses=from_addresses)

        with patch(
            "core.mda.inbound_pipeline.get_sender_trust_backend"
        ) as mock_factory:
            self._run(ctx, "reject")

        mock_factory.assert_not_called()
        assert ctx.is_spam is True

    def test_from_address_is_normalized(self):
        mailbox = factories.MailboxFactory()
        _write_to(mailbox, SENDER)
        ctx = self._ctx(mailbox, from_addresses=[{"email": "FRIEND@Partner.Example"}])

        self._run(ctx, "reject")

        assert ctx.sender_trust == SenderTrust.TRUSTED
        assert ctx.is_spam is False

    @pytest.mark.parametrize(
        "error", [SenderTrustUnavailableError("down"), RuntimeError("boom")]
    )
    def test_backend_failure_falls_back_to_spam_check(self, error):
        ctx = self._ctx(factories.MailboxFactory())

        with patch(
            "core.mda.inbound_pipeline.get_sender_trust_backend"
        ) as mock_factory:
            mock_factory.return_value.get_sender_trust.side_effect = error
            decision, _step, _rspamd = self._run(ctx, "reject")

        assert decision == Decision.CONTINUE
        assert ctx.sender_trust is None
        assert ctx.is_spam is True

    @override_settings(SENDER_TRUST_BACKEND=NULL_BACKEND)
    def test_null_backend_disables_the_floor(self):
        mailbox = factories.MailboxFactory()
        _write_to(mailbox, SENDER)
        ctx = self._ctx(mailbox)

        self._run(ctx, "reject")

        assert ctx.sender_trust == SenderTrust.UNKNOWN
        assert ctx.is_spam is True

    def test_rspamd_auth_mode_calls_rspamd_once(self):
        """inbound_auth fetches the result, the rspamd step reuses it."""
        mailbox = factories.MailboxFactory()
        _write_to(mailbox, SENDER)
        ctx = self._ctx(
            mailbox,
            spam_config={"rspamd_url": "http://rspamd:11334", "inbound_auth": "rspamd"},
        )

        decision, _step, mock_rspamd = self._run(ctx, "reject")

        assert decision == Decision.CONTINUE
        assert mock_rspamd.call_count == 1
        assert ctx.is_spam is False
        assert ctx.postmark["spam"] == "likely"
