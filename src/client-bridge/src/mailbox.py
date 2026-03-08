"""Mailbox and message implementations backed by the Messages API."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterable, Iterable
from datetime import datetime, timezone

from pymap.backend.mailbox import MailboxDataInterface, MailboxSetInterface
from pymap.concurrent import Event, ReadWriteLock
from pymap.context import subsystem
from pymap.exceptions import CloseConnection
from pymap.flags import FlagOp
from pymap.interfaces.message import CachedMessage
from pymap.listtree import ListTree
from pymap.mailbox import MailboxSnapshot
from pymap.message import BaseLoadedMessage, BaseMessage
from pymap.mime import MessageContent
from pymap.parsing.message import AppendMessage
from pymap.parsing.specials import FetchRequirement, ObjectId
from pymap.parsing.specials.flag import Deleted, Flag, Flagged, Seen
from pymap.selected import SelectedMailbox, SelectedSet

from .api.client import MessagesAPIClient, SessionExpired

__all__ = ["Message", "MailboxData", "MailboxSet"]

logger = logging.getLogger(__name__)

# Virtual folder names mapped to Messages API filters
VIRTUAL_FOLDERS = {
    "INBOX": "inbox",
    "Sent": "sent",
    "Drafts": "drafts",
    "Trash": "trash",
    "Archive": "archive",
    "Spam": "spam",
    "Starred": "starred",
}


class Message(BaseMessage):
    """A message loaded from the Messages API."""

    __slots__ = ["_api_message_id", "_content", "_recent"]

    def __init__(
        self,
        uid: int,
        internal_date: datetime,
        permanent_flags: Iterable[Flag],
        *,
        api_message_id: str,
        expunged: bool = False,
        email_id: ObjectId | None = None,
        thread_id: ObjectId | None = None,
        recent: bool = False,
        content: MessageContent | None = None,
    ) -> None:
        super().__init__(
            uid,
            internal_date,
            permanent_flags,
            expunged=expunged,
            email_id=email_id,
            thread_id=thread_id,
        )
        self._api_message_id = api_message_id
        self._content = content
        self._recent = recent

    @property
    def api_message_id(self) -> str:
        return self._api_message_id

    @property
    def recent(self) -> bool:
        return self._recent

    @recent.setter
    def recent(self, recent: bool) -> None:
        self._recent = recent

    async def load_content(self, requirement: FetchRequirement) -> LoadedMessage:
        return LoadedMessage(self, requirement, self._content)


class LoadedMessage(BaseLoadedMessage):
    pass


def _parse_date(date_str: str | None) -> datetime:
    """Parse an ISO 8601 date string, falling back to now."""
    if not date_str:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(date_str)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _flags_from_api(msg: dict) -> frozenset[Flag]:
    """Convert Messages API flags to IMAP flags."""
    flags: set[Flag] = set()
    if not msg.get("is_unread", True):
        flags.add(Seen)
    if msg.get("is_starred", False):
        flags.add(Flagged)
    if msg.get("is_trashed", False):
        flags.add(Deleted)
    return frozenset(flags)


class MailboxData(MailboxDataInterface[Message]):
    """Mailbox data backed by a Messages API folder view."""

    def __init__(
        self,
        api_client: MessagesAPIClient,
        mailbox_id: str,
        folder: str,
        *,
        readonly: bool = False,
    ) -> None:
        self._api_client = api_client
        self._api_mailbox_id = mailbox_id
        self._folder = folder
        self._mailbox_id = ObjectId.random_mailbox_id()
        self._readonly = readonly
        self._uid_validity = MailboxSnapshot.new_uid_validity()
        self._selected_set = SelectedSet()
        self._messages_lock = subsystem.get().new_rwlock()
        self._updated = subsystem.get().new_event()
        # Cache: uid -> Message
        self._messages: dict[int, Message] = {}
        self._api_id_to_uid: dict[str, int] = {}
        self._max_uid = 0
        self._loaded = False

    @property
    def mailbox_id(self) -> ObjectId:
        return self._mailbox_id

    @property
    def readonly(self) -> bool:
        return self._readonly

    @property
    def uid_validity(self) -> int:
        return self._uid_validity

    @property
    def messages_lock(self) -> ReadWriteLock:
        return self._messages_lock

    @property
    def selected_set(self) -> SelectedSet:
        return self._selected_set

    async def _load_messages(self) -> None:
        """Load messages from the Messages API into memory."""
        if self._loaded:
            return
        try:
            threads_data = await self._api_client.list_threads(self._api_mailbox_id, self._folder)
            threads = threads_data.get("results", threads_data)
            if isinstance(threads, dict):
                threads = threads.get("results", [])

            for thread in threads:
                thread_id = thread.get("id")
                if not thread_id:
                    continue
                try:
                    api_messages = await self._api_client.list_messages(
                        thread_id, self._api_mailbox_id
                    )
                except SessionExpired:
                    raise
                except Exception:
                    logger.exception("Failed to load messages for thread %s", thread_id)
                    continue

                for api_msg in api_messages:
                    msg_id = api_msg.get("id")
                    if not msg_id or msg_id in self._api_id_to_uid:
                        continue

                    self._max_uid += 1
                    uid = self._max_uid

                    # Try to load EML content
                    content = None
                    try:
                        eml_data = await self._api_client.get_message_eml(msg_id)
                        content = MessageContent.parse(eml_data)
                    except SessionExpired:
                        raise
                    except Exception:
                        logger.debug("Could not load EML for message %s", msg_id)

                    flags = _flags_from_api(api_msg)
                    internal_date = _parse_date(
                        api_msg.get("sent_at") or api_msg.get("created_at")
                    )

                    email_oid = ObjectId.random_email_id()
                    thread_oid = ObjectId.random_thread_id()

                    message = Message(
                        uid=uid,
                        internal_date=internal_date,
                        permanent_flags=flags,
                        api_message_id=msg_id,
                        email_id=email_oid,
                        thread_id=thread_oid,
                        recent=True,
                        content=content,
                    )
                    self._messages[uid] = message
                    self._api_id_to_uid[msg_id] = uid
        except SessionExpired:
            logger.info("Session expired during message load for folder %s", self._folder)
            raise CloseConnection()
        except Exception:
            logger.exception("Failed to load messages for folder %s", self._folder)
        self._loaded = True

    async def update_selected(
        self, selected: SelectedMailbox, *, wait_on: Event | None = None
    ) -> SelectedMailbox:
        await self._load_messages()
        if wait_on is not None:
            either_event = wait_on.or_event(self._updated)
            await either_event.wait()
        all_messages = list(self._messages.values())
        selected.set_messages(all_messages)
        return selected

    async def append(self, append_msg: AppendMessage, *, recent: bool = False) -> Message:
        # IMAP APPEND is not supported for API-backed mailboxes
        await self._load_messages()
        self._max_uid += 1
        uid = self._max_uid
        content = MessageContent.parse(append_msg.literal)
        email_id = ObjectId.random_email_id()
        thread_id = ObjectId.random_thread_id()
        msg = Message(
            uid=uid,
            internal_date=append_msg.when or datetime.now(timezone.utc),
            permanent_flags=append_msg.flag_set,
            api_message_id=f"local-{uid}",
            email_id=email_id,
            thread_id=thread_id,
            recent=recent,
            content=content,
        )
        self._messages[uid] = msg
        self._updated.set()
        return msg

    async def copy(
        self, uid: int, destination: MailboxData, *, recent: bool = False
    ) -> int | None:
        async with self.messages_lock.read_lock():
            source = self._messages.get(uid)
        if source is None:
            return None
        async with destination.messages_lock.write_lock():
            destination._max_uid += 1
            dest_uid = destination._max_uid
            new_msg = Message(
                uid=dest_uid,
                internal_date=source.internal_date,
                permanent_flags=source.permanent_flags,
                api_message_id=source.api_message_id,
                email_id=source.email_id,
                thread_id=source.thread_id,
                recent=recent,
                content=source._content,
            )
            destination._messages[dest_uid] = new_msg
            destination._updated.set()
        return dest_uid

    async def move(
        self, uid: int, destination: MailboxData, *, recent: bool = False
    ) -> int | None:
        async with self.messages_lock.write_lock():
            source = self._messages.pop(uid, None)
        if source is None:
            return None
        self._updated.set()
        async with destination.messages_lock.write_lock():
            destination._max_uid += 1
            dest_uid = destination._max_uid
            new_msg = Message(
                uid=dest_uid,
                internal_date=source.internal_date,
                permanent_flags=source.permanent_flags,
                api_message_id=source.api_message_id,
                email_id=source.email_id,
                thread_id=source.thread_id,
                recent=recent,
                content=source._content,
            )
            destination._messages[dest_uid] = new_msg
            destination._updated.set()
        return dest_uid

    async def get(self, uid: int, cached_msg: CachedMessage) -> Message:
        await self._load_messages()
        msg = self._messages.get(uid)
        if msg is None:
            if isinstance(cached_msg, Message):
                return Message(
                    uid=cached_msg.uid,
                    internal_date=cached_msg.internal_date,
                    permanent_flags=cached_msg.permanent_flags,
                    api_message_id=cached_msg.api_message_id,
                    expunged=True,
                    email_id=cached_msg.email_id,
                    thread_id=cached_msg.thread_id,
                )
            raise IndexError(uid)
        return msg

    async def update(
        self,
        uid: int,
        cached_msg: CachedMessage,
        flag_set: frozenset[Flag],
        mode: FlagOp,
    ) -> Message:
        msg = await self.get(uid, cached_msg)
        msg.permanent_flags = mode.apply(msg.permanent_flags, flag_set)
        self._updated.set()
        return msg

    async def delete(self, uids: Iterable[int]) -> None:
        async with self.messages_lock.write_lock():
            for uid in uids:
                self._messages.pop(uid, None)
            self._updated.set()

    async def claim_recent(self, selected: SelectedMailbox) -> None:
        await self._load_messages()
        for msg in self._messages.values():
            if msg.recent:
                msg.recent = False
                selected.session_flags.add_recent(msg.uid)

    async def cleanup(self) -> None:
        pass

    async def messages(self) -> AsyncIterable[Message]:
        await self._load_messages()
        async with self.messages_lock.read_lock():
            for msg in self._messages.values():
                yield msg

    async def snapshot(self) -> MailboxSnapshot:
        await self._load_messages()
        exists = 0
        recent = 0
        unseen = 0
        first_unseen: int | None = None
        next_uid = self._max_uid + 1
        async for msg in self.messages():
            exists += 1
            if msg.recent:
                recent += 1
            if Seen not in msg.permanent_flags:
                unseen += 1
                if first_unseen is None:
                    first_unseen = exists
        return MailboxSnapshot(
            self.mailbox_id,
            self.readonly,
            self.uid_validity,
            self.permanent_flags,
            self.session_flags,
            exists,
            recent,
            unseen,
            first_unseen,
            next_uid,
        )


class MailboxSet(MailboxSetInterface[MailboxData]):
    """Set of virtual mailboxes backed by Messages API folder views."""

    def __init__(
        self, api_client: MessagesAPIClient, mailbox_id: str, *, role: str = "sender"
    ) -> None:
        super().__init__()
        self._api_client = api_client
        self._mailbox_id = mailbox_id
        self._role = role
        self._set_lock = subsystem.get().new_rwlock()
        self._subscribed: dict[str, bool] = {name: True for name in VIRTUAL_FOLDERS}
        # Lazily create folder data
        self._folders: dict[str, MailboxData] = {}

    def _get_or_create_folder(self, name: str) -> MailboxData:
        if name not in self._folders:
            folder_key = VIRTUAL_FOLDERS.get(name, "inbox")
            self._folders[name] = MailboxData(
                self._api_client,
                self._mailbox_id,
                folder_key,
                readonly=(self._role == "reader"),
            )
        return self._folders[name]

    @property
    def delimiter(self) -> str:
        return "/"

    async def set_subscribed(self, name: str, subscribed: bool) -> None:
        async with self._set_lock.write_lock():
            self._subscribed[name] = subscribed

    async def list_subscribed(self) -> ListTree:
        async with self._set_lock.read_lock():
            names = [n for n, s in self._subscribed.items() if s]
        return ListTree(self.delimiter).update(*names)

    async def list_mailboxes(self) -> ListTree:
        return ListTree(self.delimiter).update(*VIRTUAL_FOLDERS.keys())

    async def get_mailbox(self, name: str) -> MailboxData:
        # Normalize INBOX
        lookup = name
        if name.upper() == "INBOX":
            lookup = "INBOX"
        if lookup not in VIRTUAL_FOLDERS:
            raise KeyError(name)
        return self._get_or_create_folder(lookup)

    async def add_mailbox(self, name: str) -> ObjectId:
        raise ValueError("Cannot create mailboxes on an API-backed server.")

    async def delete_mailbox(self, name: str) -> None:
        raise KeyError("Cannot delete mailboxes on an API-backed server.")

    async def rename_mailbox(self, before: str, after: str) -> None:
        raise KeyError("Cannot rename mailboxes on an API-backed server.")
