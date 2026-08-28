"""Pylint checker keeping email-address handling in ``core.mda.addresses``.

Two hand-rolled operations keep reappearing and both are wrong by default:

- Splitting on ``@`` with ``split``/``partition`` picks the *first* separator,
  which mangles a quoted local part (``"a@b"@example.com``). Even the ``r``
  variants duplicate a decision (what is a malformed address?) that belongs in
  one place.
- Unicode ``.lower()`` folds non-ASCII code points onto ASCII (U+212A KELVIN
  SIGN becomes ``k``), so it silently merges distinct addresses. That is the
  CVE-2019-19844 account-takeover class once the result reaches a mailbox
  lookup or a trust decision.

``core.mda.addresses`` is exempt: it implements the policy, so it is the one
module that has to do the raw operations.
"""

from astroid import nodes
from pylint.checkers import BaseChecker

# Implements the policy, so it is allowed the raw operations.
_POLICY_MODULE = "core.mda.addresses"

_SPLIT_METHODS = frozenset({"split", "rsplit", "partition", "rpartition"})
_CASE_METHODS = frozenset({"lower", "upper", "casefold"})

# Substrings marking a receiver as holding an address or a domain. Matched
# against the dotted source of the call's receiver, so both ``from_email`` and
# ``user.email`` hit while ``contact.name`` and ``subject`` do not.
_ADDRESS_HINTS = (
    "email",
    "addr",
    "local_part",
    "localpart",
    "domain",
)

# A party is named by an address *or* by a display name, so these only mark a
# receiver when it does not end in ``name``: ``sender_email`` is an address,
# ``sender_name`` is the human-readable label beside it.
_PARTY_HINTS = ("sender", "recipient")


def _dotted(node) -> str:
    """Best-effort dotted source of an expression, or ``""``."""
    if isinstance(node, nodes.Name):
        return node.name
    if isinstance(node, nodes.Attribute):
        base = _dotted(node.expr)
        return f"{base}.{node.attrname}" if base else node.attrname
    if isinstance(node, nodes.Call):
        return _dotted(node.func)
    if isinstance(node, nodes.Subscript):
        return _dotted(node.value)
    return ""


class AddressOpsChecker(BaseChecker):
    """Flag hand-rolled address splitting and Unicode case folding."""

    name = "address-ops"
    msgs = {
        "W9901": (
            "Hand-rolled address split on '@'; use core.mda.addresses "
            "(split_address, address_local_part, address_domain)",
            "address-manual-split",
            "Splitting an address by hand gets the quoted-local-part case "
            "wrong and spreads the malformed-address decision around.",
        ),
        "W9902": (
            "Unicode case fold on an address; use core.mda.addresses "
            "(ascii_lower, normalize_address, normalize_domain)",
            "address-unicode-case",
            "str.lower() maps non-ASCII code points onto ASCII, so it merges "
            "addresses that are not the same (CVE-2019-19844 class).",
        ),
    }

    def visit_call(self, node: nodes.Call) -> None:
        """Check one method call."""
        if node.root().name == _POLICY_MODULE:
            return
        if not isinstance(node.func, nodes.Attribute):
            return

        method = node.func.attrname
        if method in _SPLIT_METHODS and self._splits_on_at(node):
            self.add_message("address-manual-split", node=node)
        elif method in _CASE_METHODS and self._is_address(node.func.expr):
            self.add_message("address-unicode-case", node=node)

    @staticmethod
    def _splits_on_at(node: nodes.Call) -> bool:
        """True when the first argument is the literal ``"@"``."""
        return bool(
            node.args
            and isinstance(node.args[0], nodes.Const)
            and node.args[0].value == "@"
        )

    @staticmethod
    def _is_address(receiver) -> bool:
        """True when the receiver's dotted source names an address."""
        dotted = _dotted(receiver).lower()
        if any(hint in dotted for hint in _ADDRESS_HINTS):
            return True
        if not any(hint in dotted for hint in _PARTY_HINTS):
            return False
        # Per segment, not on the whole string: a chained call puts the method
        # last, so the receiver of ``sender_name.strip().lower()`` reads as
        # ``sender_name.strip`` and would otherwise lose the exclusion.
        return not any(part.endswith("name") for part in dotted.split("."))


def register(linter) -> None:
    """Entry point called by pylint's plugin loader."""
    linter.register_checker(AddressOpsChecker(linter))
