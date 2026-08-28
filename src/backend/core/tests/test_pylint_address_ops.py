"""Tests for the ``pylint_address_ops`` checker.

The checker is what keeps hand-rolled address handling from coming back, so
it needs its own guard: a silent regression in it would be invisible (lint
stays green precisely because nothing is reported).
"""

import astroid
import pytest

from pylint_address_ops import AddressOpsChecker

SPLIT = "address-manual-split"
CASE = "address-unicode-case"


class _RecordingChecker(AddressOpsChecker):
    """Collect message ids instead of reporting them to a linter."""

    # pylint: disable=super-init-not-called
    def __init__(self):
        self.fired = []

    def add_message(self, msgid, *args, **kwargs):
        self.fired.append(msgid)


def _check(source: str, module_name: str = "core.some_module") -> list[str]:
    """Run the checker over every call in *source*."""
    checker = _RecordingChecker()
    module = astroid.parse(source, module_name=module_name)
    for node in module.nodes_of_class(astroid.nodes.Call):
        checker.visit_call(node)
    return checker.fired


@pytest.mark.parametrize(
    "source",
    [
        'email.split("@")',
        'email.rsplit("@", 1)',
        'email.partition("@")',
        'email.rpartition("@")[2]',
        'user.email.rpartition("@")[0]',
        'self.sender.email.rpartition("@")[2]',
        'some_address.strip().split("@")',
        # Receiver-agnostic: a literal '@' separator is decisive on its own,
        # so the rule does not depend on the variable being named like one.
        'text.rpartition("@")',
    ],
)
def test_flags_manual_split(source):
    """Any split on a literal '@' is reported."""
    assert _check(source) == [SPLIT]


@pytest.mark.parametrize(
    "source",
    [
        "email.lower()",
        "user.email.lower()",
        "from_email.strip().rstrip('.').lower()",
        "sender_email.casefold()",
        # The address half of the same pair still fires through a chain.
        "sender_email.strip().lower()",
        "recipient_email.strip().lower()",
        "recipient.upper()",
        "domain.lower()",
        "local_part.lower()",
    ],
)
def test_flags_unicode_case_fold(source):
    """Case folding an address-ish receiver is reported."""
    assert _check(source) == [CASE]


@pytest.mark.parametrize(
    "source",
    [
        # Not an address.
        "contact.name.lower()",
        "subject.lower()",
        "header_name.lower()",
        "value.casefold()",
        # A party is named by an address or by a display name; only the
        # address side is ours to fold.
        "sender_name.lower()",
        "recipient_name.lower()",
        "message.sender_name.lower()",
        # Chained: the receiver reads as ``sender_name.strip``, so the
        # exclusion has to survive the method on the end.
        "sender_name.strip().lower()",
        "recipient_name.strip().casefold()",
        "message.sender_name.strip().lower()",
        # Not a split on '@'.
        'email.split(",")',
        "email.split()",
        # ASCII folding is the sanctioned helper, not a violation.
        "ascii_lower(email)",
    ],
)
def test_ignores_unrelated_calls(source):
    """Non-address receivers and non-'@' splits stay silent."""
    assert _check(source) == []


def test_policy_module_is_exempt():
    """``core.mda.addresses`` implements the policy, so it may do both."""
    source = 'email.rpartition("@")\nemail.lower()'
    assert _check(source, module_name="core.mda.addresses") == []
    assert _check(source) == [SPLIT, CASE]
