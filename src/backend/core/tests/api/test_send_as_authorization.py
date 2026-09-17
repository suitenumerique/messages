"""Who may send as which mailbox.

``senderId`` is chosen by the client, and the mailbox it names decides both
the ``From`` header and the DKIM key. Everything here is about the gap between
"this user can send *something* on this thread" and "this user may send *as
this mailbox*".
"""

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models

pytestmark = pytest.mark.django_db


def _draft_for(mailbox, user):
    """A draft on a thread the mailbox can edit, composed by that mailbox."""
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    sender = factories.ContactFactory(mailbox=mailbox, email=str(mailbox))
    message = factories.MessageFactory(
        thread=thread, sender=sender, is_draft=True, is_sender=True
    )
    factories.MessageRecipientFactory(
        message=message,
        contact=factories.ContactFactory(email="rcpt@elsewhere.test"),
        type=enums.MessageRecipientTypeChoices.TO,
    )
    return message


def _send(user, message, sender_mailbox):
    client = APIClient()
    client.force_authenticate(user=user)
    return client.post(
        reverse("send-message"),
        {"messageId": str(message.id), "senderId": str(sender_mailbox.id)},
        format="json",
    )


def test_cannot_send_as_a_mailbox_with_no_access_at_all():
    """The plainest case: a mailbox the user has no relationship with."""
    attacker = factories.UserFactory()
    victim_mailbox = factories.MailboxFactory()
    attacker_mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=attacker_mailbox, user=attacker, role=enums.MailboxRoleChoices.SENDER
    )
    message = _draft_for(attacker_mailbox, attacker)
    # The victim mailbox can also edit this thread, so only the send-as check
    # stands between the attacker and a message signed as the victim.
    factories.ThreadAccessFactory(
        mailbox=victim_mailbox,
        thread=message.thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )

    response = _send(attacker, message, victim_mailbox)

    assert response.status_code in (
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    )


def test_viewer_on_the_mailbox_cannot_send_as_it():
    """A read role on the mailbox is not a licence to sign as it.

    The dangerous shape is the query matching the user's access row and the
    sending role on two *different* rows: this mailbox has a genuine SENDER —
    another user — so a per-row check passes only if the attacker's own row
    carries the role.
    """
    attacker = factories.UserFactory()
    colleague = factories.UserFactory()
    shared_mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=shared_mailbox, user=attacker, role=enums.MailboxRoleChoices.VIEWER
    )
    factories.MailboxAccessFactory(
        mailbox=shared_mailbox, user=colleague, role=enums.MailboxRoleChoices.SENDER
    )

    # The attacker can send through a mailbox of their own on the same thread,
    # so IsAllowedToAccess is satisfied and only the send-as check remains.
    attacker_mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=attacker_mailbox, user=attacker, role=enums.MailboxRoleChoices.SENDER
    )
    message = _draft_for(attacker_mailbox, attacker)
    factories.ThreadAccessFactory(
        mailbox=shared_mailbox,
        thread=message.thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )

    response = _send(attacker, message, shared_mailbox)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    message.refresh_from_db()
    assert message.is_draft is True


def test_sender_role_on_the_mailbox_may_send_as_it():
    """The legitimate case still works, so the checks above are not vacuous."""
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=enums.MailboxRoleChoices.SENDER
    )
    message = _draft_for(mailbox, user)

    response = _send(user, message, mailbox)

    assert response.status_code == status.HTTP_200_OK


def test_from_becomes_the_sending_mailbox_not_the_drafting_one():
    """Replying from a second mailbox signs with that mailbox's key.

    The rewrite is what keeps the pair aligned; without it the message would
    keep the drafting mailbox's From and be signed for the sending one.
    """
    user = factories.UserFactory()
    drafting_mailbox = factories.MailboxFactory()
    sending_mailbox = factories.MailboxFactory()
    for mailbox in (drafting_mailbox, sending_mailbox):
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=enums.MailboxRoleChoices.SENDER
        )
    message = _draft_for(drafting_mailbox, user)
    factories.ThreadAccessFactory(
        mailbox=sending_mailbox,
        thread=message.thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )

    response = _send(user, message, sending_mailbox)

    assert response.status_code == status.HTTP_200_OK
    message.refresh_from_db()
    assert message.sender.email == str(sending_mailbox)


def test_the_rewrite_cannot_be_steered_to_an_unowned_mailbox():
    """The rewrite follows authorization, it does not bypass it.

    ``_realign_sender_with_sending_mailbox`` sets From to whatever mailbox is
    sending, so it must run only after the send-as check has approved it.
    """
    attacker = factories.UserFactory()
    victim_mailbox = factories.MailboxFactory()
    attacker_mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=attacker_mailbox, user=attacker, role=enums.MailboxRoleChoices.SENDER
    )
    message = _draft_for(attacker_mailbox, attacker)
    factories.ThreadAccessFactory(
        mailbox=victim_mailbox,
        thread=message.thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    original_sender_id = message.sender_id

    _send(attacker, message, victim_mailbox)

    message.refresh_from_db()
    assert message.sender_id == original_sender_id
    assert not models.Contact.objects.filter(mailbox=victim_mailbox).exists()
