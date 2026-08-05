"""``send_mail`` must report a compose failure as a CommandError.

``compose_email`` is strict: a malformed addr-spec, or a non-ASCII local
part needing SMTPUTF8, raises rather than emitting something unroutable.
Unhandled, that reached the operator as a bare traceback — while the very
same class of problem caught one step earlier (``parse_address``) already
produced a clean ``CommandError``. Same failure, same reporting.
"""

from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError

import pytest
from jmap_email import InvalidAddressError

pytestmark = pytest.mark.django_db


def test_compose_error_becomes_a_command_error():
    with patch(
        "core.management.commands.send_mail.compose_email",
        side_effect=InvalidAddressError("Non-ASCII local-part in 'to'"),
    ):
        with pytest.raises(CommandError, match="Cannot compose message"):
            call_command(
                "send_mail",
                "--from",
                "sender@example.com",
                "--to",
                "recipient@example.com",
                "--subject",
                "hi",
                "--body",
                "hello",
            )


def test_invalid_address_still_reported_before_compose():
    """The pre-existing path is unchanged: a shape failure is caught by
    ``parse_address`` and never reaches the composer."""
    with pytest.raises(CommandError, match="Invalid recipient email address"):
        call_command(
            "send_mail",
            "--from",
            "sender@example.com",
            "--to",
            "not-an-address",
            "--subject",
            "hi",
            "--body",
            "hello",
        )
