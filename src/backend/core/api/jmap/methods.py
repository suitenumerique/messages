"""JMAP method registry and handlers."""

from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Any

from django.db.models import OuterRef, Subquery, Value
from django.db.models.functions import Concat

from core import models

from .errors import (
    InvalidArgumentsError,
    InvalidResultReferenceError,
    UnknownMethodError,
)


class MethodRegistry:
    """Registry for JMAP method handlers."""

    _methods: dict[str, type["BaseMethod"]] = {}

    @classmethod
    def register(cls, method_name: str):
        """Decorator to register a method handler."""

        def decorator(handler_class: type["BaseMethod"]):
            cls._methods[method_name] = handler_class
            return handler_class

        return decorator

    @classmethod
    def get_handler(cls, method_name: str) -> type["BaseMethod"]:
        """Get a method handler by name."""
        handler = cls._methods.get(method_name)
        if not handler:
            raise UnknownMethodError(f"Unknown method: {method_name}")
        return handler

    @classmethod
    def get_registered_methods(cls) -> list[str]:
        """Get list of registered method names."""
        return list(cls._methods.keys())


class JMAPContext:
    """Execution context for JMAP methods."""

    def __init__(self, user, results_by_call_id: dict[str, dict]):
        self.user = user
        self.results_by_call_id = results_by_call_id


class BaseMethod:
    """Base class for JMAP method handlers."""

    def __init__(self, context: JMAPContext):
        self.context = context

    def execute(self, args: dict) -> dict:
        """Execute the method with the given arguments."""
        raise NotImplementedError

    def _get_state(self) -> str:
        """Get current state string (timestamp-based)."""
        return datetime.now(dt_timezone.utc).isoformat()

    def _get_account_id(self, args: dict) -> str:
        """Get and validate accountId from args."""
        account_id = args.get("accountId")
        if account_id is None:
            raise InvalidArgumentsError("Missing required argument: accountId")
        # Account ID should match the user's ID
        if str(self.context.user.id) != str(account_id):
            raise InvalidArgumentsError(f"Invalid accountId: {account_id}")
        return account_id

    def resolve_value(self, value: Any) -> Any:
        """Resolve a value, handling back-references."""
        if isinstance(value, dict) and "resultOf" in value:
            return self._resolve_reference(value)
        return value

    def _resolve_reference(self, ref: dict) -> Any:
        """Resolve a single ResultReference."""
        call_id = ref.get("resultOf")
        path = ref.get("path", "/")

        if call_id not in self.context.results_by_call_id:
            raise InvalidResultReferenceError(f"Unknown callId: {call_id}")

        result = self.context.results_by_call_id[call_id]
        return self._navigate_path(result, path)

    def _navigate_path(self, obj: Any, path: str) -> Any:
        """Navigate a JSONPointer-like path."""
        if path in ("/", ""):
            return obj

        parts = path.strip("/").split("/")
        current = obj

        for part in parts:
            if part == "*":
                # Wildcard: extract from all items in list
                if not isinstance(current, list):
                    raise InvalidResultReferenceError(
                        f"Cannot use '*' on non-list: {type(current)}"
                    )
                # Return the remaining path applied to each item
                remaining_parts = parts[parts.index(part) + 1 :]
                if remaining_parts:
                    remaining_path = "/" + "/".join(remaining_parts)
                    return [
                        self._navigate_path(item, remaining_path) for item in current
                    ]
                return current
            elif isinstance(current, dict):
                if part not in current:
                    raise InvalidResultReferenceError(f"Key not found: {part}")
                current = current[part]
            elif isinstance(current, list):
                try:
                    idx = int(part)
                    current = current[idx]
                except (ValueError, IndexError) as e:
                    raise InvalidResultReferenceError(
                        f"Invalid list index: {part}"
                    ) from e
            else:
                raise InvalidResultReferenceError(
                    f"Cannot navigate into {type(current)}"
                )

        return current


def resolve_args(args: dict, context: JMAPContext) -> dict:
    """Resolve all back-references in method arguments."""
    resolved = {}
    base_method = BaseMethod(context)

    for key, value in args.items():
        if key.startswith("#"):
            # Key starts with # - this is a back-reference
            actual_key = key[1:]
            resolved[actual_key] = base_method.resolve_value(value)
        else:
            resolved[key] = value

    return resolved


# ---------- Mailbox Methods ----------


@MethodRegistry.register("Mailbox/query")
class MailboxQuery(BaseMethod):
    """Query mailboxes accessible to the user."""

    def execute(self, args: dict) -> dict:
        account_id = self._get_account_id(args)
        filter_obj = args.get("filter", {})
        position = args.get("position", 0)
        limit = args.get("limit")

        # Get user's accessible mailboxes
        mailboxes = models.Mailbox.objects.filter(accesses__user=self.context.user)

        # Apply name filter
        if name_filter := filter_obj.get("name"):
            # Annotate with full email address and filter
            mailboxes = mailboxes.annotate(
                full_email=Concat("local_part", Value("@"), "domain__name")
            ).filter(full_email__icontains=name_filter)

        # Get IDs
        mailbox_ids = list(mailboxes.values_list("id", flat=True))

        # Apply pagination
        if limit is not None:
            mailbox_ids = mailbox_ids[position : position + limit]
        else:
            mailbox_ids = mailbox_ids[position:]

        return {
            "accountId": account_id,
            "queryState": self._get_state(),
            "canCalculateChanges": False,
            "position": position,
            "ids": [str(mid) for mid in mailbox_ids],
            "total": len(mailbox_ids),
        }


@MethodRegistry.register("Mailbox/get")
class MailboxGet(BaseMethod):
    """Get mailbox details by IDs."""

    def execute(self, args: dict) -> dict:
        account_id = self._get_account_id(args)
        ids = self.resolve_value(args.get("ids"))
        properties = args.get("properties")

        if ids is None:
            # If ids is null, return all mailboxes
            mailboxes = models.Mailbox.objects.filter(accesses__user=self.context.user)
        else:
            # Fetch specific mailboxes with access check
            mailboxes = models.Mailbox.objects.filter(
                id__in=ids, accesses__user=self.context.user
            )

        result_list = []
        for mailbox in mailboxes.select_related("domain"):
            mailbox_data = self._serialize_mailbox(mailbox, properties)
            result_list.append(mailbox_data)

        found_ids = {str(m.id) for m in mailboxes}
        not_found = [mid for mid in (ids or []) if mid not in found_ids]

        return {
            "accountId": account_id,
            "state": self._get_state(),
            "list": result_list,
            "notFound": not_found,
        }

    def _serialize_mailbox(
        self, mailbox: models.Mailbox, properties: list | None
    ) -> dict:
        """Serialize a mailbox to JMAP format."""
        full_email = f"{mailbox.local_part}@{mailbox.domain.name}"

        data = {
            "id": str(mailbox.id),
            "name": full_email,
            "role": None,  # Could map to is_identity or other logic
            "sortOrder": 0,
            "totalEmails": models.Message.objects.filter(
                thread__accesses__mailbox=mailbox
            ).count(),
            "unreadEmails": models.Message.objects.filter(
                thread__accesses__mailbox=mailbox, is_unread=True
            ).count(),
            "totalThreads": models.Thread.objects.filter(
                accesses__mailbox=mailbox
            ).count(),
            "unreadThreads": models.Thread.objects.filter(
                accesses__mailbox=mailbox, has_unread=True
            ).count(),
            "myRights": {
                "mayReadItems": True,
                "mayAddItems": True,
                "mayRemoveItems": True,
                "maySetSeen": True,
                "maySetKeywords": True,
                "mayCreateChild": False,
                "mayRename": False,
                "mayDelete": False,
                "maySubmit": True,
            },
            "isSubscribed": True,
        }

        if properties:
            return {k: v for k, v in data.items() if k in properties or k == "id"}
        return data


# ---------- Email Methods ----------


@MethodRegistry.register("Email/query")
class EmailQuery(BaseMethod):
    """Query emails with filters and sorting."""

    def execute(self, args: dict) -> dict:
        account_id = self._get_account_id(args)
        filter_obj = args.get("filter", {})
        sort = args.get("sort", [{"property": "receivedAt", "isAscending": False}])
        collapse_threads = args.get("collapseThreads", False)
        position = args.get("position", 0)
        limit = args.get("limit", 50)

        # Base queryset - messages user has access to
        messages = models.Message.objects.filter(
            thread__accesses__mailbox__accesses__user=self.context.user
        )

        # Apply filters
        if in_mailbox := self.resolve_value(filter_obj.get("inMailbox")):
            messages = messages.filter(thread__accesses__mailbox_id=in_mailbox)

        if after := filter_obj.get("after"):
            # Parse ISO date string
            if isinstance(after, str):
                after = datetime.fromisoformat(after.replace("Z", "+00:00"))
            messages = messages.filter(created_at__gte=after)

        if before := filter_obj.get("before"):
            if isinstance(before, str):
                before = datetime.fromisoformat(before.replace("Z", "+00:00"))
            messages = messages.filter(created_at__lt=before)

        # Apply sorting
        order_by = self._build_order_by(sort)
        messages = messages.order_by(*order_by)

        # Collapse threads if requested (return only latest email per thread)
        if collapse_threads:
            # For each thread, get the latest message that matches the filters
            # We need to get the max id from the filtered set, grouped by thread

            # Get the ID of the latest filtered message per thread
            latest_filtered_per_thread = (
                messages.filter(thread_id=OuterRef("thread_id"))
                .order_by("-created_at")
                .values("id")[:1]
            )

            # Keep only messages that are the latest filtered message in their thread
            messages = messages.filter(
                id=Subquery(latest_filtered_per_thread)
            ).distinct()

        # Get total before pagination
        total = messages.count()

        # Apply pagination
        message_ids = list(
            messages.values_list("id", flat=True)[position : position + limit]
        )

        return {
            "accountId": account_id,
            "queryState": self._get_state(),
            "canCalculateChanges": False,
            "position": position,
            "ids": [str(mid) for mid in message_ids],
            "total": total,
        }

    def _build_order_by(self, sort: list) -> list:
        """Build Django order_by from JMAP sort."""
        property_map = {
            "receivedAt": "created_at",
            "sentAt": "sent_at",
            "size": "blob__size",
            "subject": "subject",
        }

        order_by = []
        for sort_item in sort:
            prop = sort_item.get("property", "receivedAt")
            is_ascending = sort_item.get("isAscending", False)

            django_field = property_map.get(prop, "created_at")
            if not is_ascending:
                django_field = f"-{django_field}"
            order_by.append(django_field)

        return order_by or ["-created_at"]


@MethodRegistry.register("Email/get")
class EmailGet(BaseMethod):
    """Get email details by IDs."""

    def execute(self, args: dict) -> dict:
        account_id = self._get_account_id(args)
        ids = self.resolve_value(args.get("ids"))
        properties = args.get("properties")

        if ids is None:
            raise InvalidArgumentsError("ids is required for Email/get")

        messages = (
            models.Message.objects.filter(id__in=ids)
            .filter(thread__accesses__mailbox__accesses__user=self.context.user)
            .select_related("sender", "thread", "blob")
            .prefetch_related("recipients__contact")
        )

        result_list = []
        for message in messages:
            email_data = self._serialize_email(message, properties)
            result_list.append(email_data)

        found_ids = {str(m.id) for m in messages}
        not_found = [mid for mid in ids if mid not in found_ids]

        return {
            "accountId": account_id,
            "state": self._get_state(),
            "list": result_list,
            "notFound": not_found,
        }

    def _serialize_email(
        self, message: models.Message, properties: list | None
    ) -> dict:
        """Serialize a message to JMAP Email format."""
        # Get mailbox IDs for this message's thread
        mailbox_ids = list(
            models.ThreadAccess.objects.filter(thread=message.thread).values_list(
                "mailbox_id", flat=True
            )
        )

        # Build keywords from flags
        keywords = {}
        if not message.is_unread:
            keywords["$seen"] = True
        if message.is_starred:
            keywords["$flagged"] = True
        if message.is_draft:
            keywords["$draft"] = True

        # Get recipients by type
        recipients_to = []
        recipients_cc = []
        recipients_bcc = []
        for recipient in message.recipients.all():
            addr = {"name": recipient.contact.name, "email": recipient.contact.email}
            if recipient.type == models.MessageRecipientTypeChoices.TO:
                recipients_to.append(addr)
            elif recipient.type == models.MessageRecipientTypeChoices.CC:
                recipients_cc.append(addr)
            elif recipient.type == models.MessageRecipientTypeChoices.BCC:
                recipients_bcc.append(addr)

        # Get preview from thread snippet or generate one
        preview = message.thread.snippet[:256] if message.thread.snippet else ""

        data = {
            "id": str(message.id),
            "blobId": str(message.blob.id) if message.blob else None,
            "threadId": str(message.thread.id),
            "mailboxIds": {str(mid): True for mid in mailbox_ids},
            "keywords": keywords,
            "size": message.blob.size if message.blob else 0,
            "receivedAt": (
                message.created_at.isoformat() if message.created_at else None
            ),
            "sentAt": message.sent_at.isoformat() if message.sent_at else None,
            "from": (
                [{"name": message.sender.name, "email": message.sender.email}]
                if message.sender
                else []
            ),
            "to": recipients_to,
            "cc": recipients_cc,
            "bcc": recipients_bcc,
            "subject": message.subject,
            "preview": preview,
            "hasAttachment": message.has_attachments,
        }

        if properties:
            return {k: v for k, v in data.items() if k in properties or k == "id"}
        return data


# ---------- Thread Methods ----------


@MethodRegistry.register("Thread/get")
class ThreadGet(BaseMethod):
    """Get thread details by IDs."""

    def execute(self, args: dict) -> dict:
        account_id = self._get_account_id(args)
        ids = self.resolve_value(args.get("ids"))

        if ids is None:
            raise InvalidArgumentsError("ids is required for Thread/get")

        threads = models.Thread.objects.filter(
            id__in=ids, accesses__mailbox__accesses__user=self.context.user
        ).prefetch_related("messages")

        result_list = []
        for thread in threads:
            thread_data = {
                "id": str(thread.id),
                "emailIds": [str(m.id) for m in thread.messages.order_by("created_at")],
            }
            result_list.append(thread_data)

        found_ids = {str(t.id) for t in threads}
        not_found = [tid for tid in ids if tid not in found_ids]

        return {
            "accountId": account_id,
            "state": self._get_state(),
            "list": result_list,
            "notFound": not_found,
        }
