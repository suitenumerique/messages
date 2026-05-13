"""IMAP session implementation for the Messages API backend."""

from __future__ import annotations

from typing import Any

from pymap.backend.session import BaseSession
from pymap.config import IMAPConfig
from pymap.interfaces.filter import FilterSetInterface

from .mailbox import MailboxSet, Message


class Session(BaseSession[Message]):
    """Session for the Messages API backend.

    Reuses pymap's BaseSession which provides default implementations for all
    session operations based on the MailboxSet/MailboxData interfaces.
    """

    def __init__(self, owner: str, config: IMAPConfig, mailbox_set: MailboxSet) -> None:
        super().__init__(owner)
        self._config = config
        self._mailbox_set = mailbox_set

    @property
    def config(self) -> IMAPConfig:
        return self._config

    @property
    def mailbox_set(self) -> MailboxSet:
        return self._mailbox_set

    @property
    def filter_set(self) -> FilterSetInterface[Any] | None:
        return None
