"""Mutable process state, as opposed to the environment-driven configuration.

Only drain, so far. It lives here rather than in :mod:`pymta.settings` because
settings is what the operator wrote down and this is what the process has since
decided; conflating the two makes it impossible to tell an operator's intent
from a signal's side effect.
"""

from __future__ import annotations

from . import settings

_shutting_down = False


def request_shutdown() -> None:
    """Enter drain permanently. Called from the SIGTERM/SIGINT handler.

    One way. A second signal does not undo it, and neither does a SIGHUP that
    clears ``PYMTA_DRAIN``: once the process has committed to exiting, telling
    senders it is open again would accept mail it is about to stop serving.
    """
    global _shutting_down  # noqa: PLW0603
    _shutting_down = True


def reset_for_tests() -> None:
    global _shutting_down  # noqa: PLW0603
    _shutting_down = False


def is_draining() -> bool:
    """True when new sessions must be refused.

    Either because a shutdown is under way, or because ``PYMTA_DRAIN`` says so
    on its own. The env var is the "drain but keep running" case: pulling a node
    out of rotation to look at it, without ending the process.
    """
    return _shutting_down or settings.PYMTA_DRAIN


def is_shutting_down() -> bool:
    return _shutting_down
