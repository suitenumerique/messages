"""Mutable process state, as opposed to the environment-driven configuration.

Only the shutdown flag, so far. It lives here rather than in
:mod:`pymta.settings` because settings is what the operator wrote down and
this is what the process has since decided.
"""

from __future__ import annotations

_shutting_down = False


def request_shutdown() -> None:
    """Start refusing new sessions. Called from the SIGTERM/SIGINT handler.

    One way: a second signal does not undo it. Once the process has committed
    to exiting, telling senders it is open again would accept mail it is about
    to stop serving.
    """
    global _shutting_down  # noqa: PLW0603
    _shutting_down = True


def reset_for_tests() -> None:
    global _shutting_down  # noqa: PLW0603
    _shutting_down = False


def is_shutting_down() -> bool:
    return _shutting_down
