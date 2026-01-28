"""JMAP method registry and handlers."""

import json
import logging
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Any

from django.db.models import OuterRef, Subquery, Value
from django.db.models.functions import Concat
from django.utils import timezone

from core import models
from core.mda.draft import create_draft
from core.mda.outbound import prepare_outbound_message
from core.mda.outbound_tasks import send_message_task

from .errors import (
    InvalidArgumentsError,
    InvalidResultReferenceError,
    UnknownMethodError,
)

logger = logging.getLogger(__name__)


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
        self.implicit_responses: list[list] = []
        self.current_call_id: str = ""


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


# ---------- Email/set Method ----------


@MethodRegistry.register("Email/set")
class EmailSetMethod(BaseMethod):
    """Create, update, and destroy emails."""

    def execute(self, args: dict) -> dict:
        account_id = self._get_account_id(args)
        create = args.get("create", {})
        update = args.get("update", {})
        destroy = args.get("destroy", [])

        created = {}
        not_created = {}
        updated = {}
        not_updated = {}
        destroyed = []
        not_destroyed = {}

        # --- create ---
        for creation_id, email_data in create.items():
            try:
                created[creation_id] = self._create_email(email_data)
            except Exception as e:
                not_created[creation_id] = {
                    "type": "invalidArguments",
                    "description": str(e),
                }

        # --- update ---
        for email_id, patch in update.items():
            try:
                self._update_email(email_id, patch)
                updated[email_id] = None
            except Exception as e:
                not_updated[email_id] = {
                    "type": "invalidArguments",
                    "description": str(e),
                }

        # --- destroy ---
        for email_id in destroy:
            try:
                self._destroy_email(email_id)
                destroyed.append(email_id)
            except Exception as e:
                not_destroyed[email_id] = {
                    "type": "invalidArguments",
                    "description": str(e),
                }

        return {
            "accountId": account_id,
            "oldState": self._get_state(),
            "newState": self._get_state(),
            "created": created or None,
            "notCreated": not_created or None,
            "updated": updated or None,
            "notUpdated": not_updated or None,
            "destroyed": destroyed or None,
            "notDestroyed": not_destroyed or None,
        }

    def _create_email(self, email_data: dict) -> dict:
        """Create a draft email from JMAP Email data."""
        mailbox_ids = email_data.get("mailboxIds", {})
        if not mailbox_ids:
            raise InvalidArgumentsError("mailboxIds is required")

        # Get the first mailbox the user has access to
        mailbox_id = next(iter(mailbox_ids))
        try:
            mailbox = models.Mailbox.objects.get(
                id=mailbox_id, accesses__user=self.context.user
            )
        except models.Mailbox.DoesNotExist:
            raise InvalidArgumentsError(f"Mailbox not found: {mailbox_id}")

        # Extract fields
        subject = email_data.get("subject", "")
        to_list = email_data.get("to", [])
        cc_list = email_data.get("cc", [])
        bcc_list = email_data.get("bcc", [])

        to_emails = [addr["email"] for addr in to_list if "email" in addr]
        cc_emails = [addr["email"] for addr in cc_list if "email" in addr]
        bcc_emails = [addr["email"] for addr in bcc_list if "email" in addr]

        # Extract body content from JMAP bodyValues / textBody / htmlBody
        text_body = ""
        html_body = ""
        body_values = email_data.get("bodyValues", {})
        for part in email_data.get("textBody", []):
            part_id = part.get("partId")
            if part_id and part_id in body_values:
                text_body = body_values[part_id].get("value", "")
        for part in email_data.get("htmlBody", []):
            part_id = part.get("partId")
            if part_id and part_id in body_values:
                html_body = body_values[part_id].get("value", "")

        # Store body as JSON in draft_blob so EmailSubmission can read it back
        draft_body = json.dumps(
            {"format": "jmap", "textBody": text_body, "htmlBody": html_body}
        )

        message = create_draft(
            mailbox=mailbox,
            subject=subject,
            draft_body=draft_body,
            to_emails=to_emails,
            cc_emails=cc_emails,
            bcc_emails=bcc_emails,
        )

        return {
            "id": str(message.id),
            "blobId": str(message.draft_blob.id) if message.draft_blob else None,
            "threadId": str(message.thread_id),
            "size": message.draft_blob.size if message.draft_blob else 0,
        }

    def _update_email(self, email_id: str, patch: dict) -> None:
        """Update email keywords/flags via JMAP patch syntax."""
        message = models.Message.objects.filter(
            id=email_id,
            thread__accesses__mailbox__accesses__user=self.context.user,
        ).first()
        if not message:
            raise InvalidArgumentsError(f"Email not found: {email_id}")

        updated_fields = []

        # Handle full keywords replacement
        if "keywords" in patch:
            keywords = patch["keywords"]
            is_seen = keywords.get("$seen", False)
            is_flagged = keywords.get("$flagged", False)
            is_draft = keywords.get("$draft", False)

            message.is_unread = not is_seen
            message.is_starred = is_flagged
            message.is_draft = is_draft
            updated_fields.extend(["is_unread", "is_starred", "is_draft"])

        # Handle individual keyword patches (e.g. "keywords/$seen": True)
        for key, value in patch.items():
            if key == "keywords/$seen":
                message.is_unread = not value
                if "is_unread" not in updated_fields:
                    updated_fields.append("is_unread")
            elif key == "keywords/$flagged":
                message.is_starred = value
                if "is_starred" not in updated_fields:
                    updated_fields.append("is_starred")
            elif key == "keywords/$draft":
                message.is_draft = value
                if "is_draft" not in updated_fields:
                    updated_fields.append("is_draft")

        if updated_fields:
            message.save(update_fields=updated_fields + ["updated_at"])

    def _destroy_email(self, email_id: str) -> None:
        """Trash a message (sets is_trashed=True)."""
        message = models.Message.objects.filter(
            id=email_id,
            thread__accesses__mailbox__accesses__user=self.context.user,
        ).first()
        if not message:
            raise InvalidArgumentsError(f"Email not found: {email_id}")

        message.is_trashed = True
        message.trashed_at = timezone.now()
        message.save(update_fields=["is_trashed", "trashed_at", "updated_at"])


# ---------- EmailSubmission/set Method ----------


@MethodRegistry.register("EmailSubmission/set")
class EmailSubmissionSetMethod(BaseMethod):
    """Submit emails for delivery."""

    def execute(self, args: dict) -> dict:
        account_id = self._get_account_id(args)
        create = args.get("create", {})
        on_success_update = args.get("onSuccessUpdateEmail", {})

        created = {}
        not_created = {}

        for creation_id, submission_data in create.items():
            try:
                result = self._create_submission(submission_data)
                created[creation_id] = result

                # Handle onSuccessUpdateEmail
                if on_success_update:
                    self._apply_on_success(
                        result["emailId"], creation_id, on_success_update
                    )
            except Exception as e:
                logger.exception(
                    "Failed to create email submission %s: %s", creation_id, e
                )
                not_created[creation_id] = {
                    "type": "invalidArguments",
                    "description": str(e),
                }

        return {
            "accountId": account_id,
            "oldState": self._get_state(),
            "newState": self._get_state(),
            "created": created or None,
            "notCreated": not_created or None,
        }

    def _create_submission(self, submission_data: dict) -> dict:
        """Submit a draft email for delivery."""
        email_id = submission_data.get("emailId")
        identity_id = submission_data.get("identityId")

        if not email_id:
            raise InvalidArgumentsError("emailId is required")
        if not identity_id:
            raise InvalidArgumentsError("identityId is required")

        # Validate identity (mailbox)
        try:
            mailbox = models.Mailbox.objects.get(
                id=identity_id, accesses__user=self.context.user
            )
        except models.Mailbox.DoesNotExist:
            raise InvalidArgumentsError(f"Identity not found: {identity_id}")

        # Get the draft message
        message = models.Message.objects.filter(
            id=email_id,
            thread__accesses__mailbox__accesses__user=self.context.user,
        ).select_related("thread", "sender", "draft_blob", "signature").first()
        if not message:
            raise InvalidArgumentsError(f"Email not found: {email_id}")
        if not message.is_draft:
            raise InvalidArgumentsError("Email is not a draft")

        # Read body from draft_blob
        text_body = ""
        html_body = ""
        if message.draft_blob:
            try:
                blob_content = message.draft_blob.get_content()
                body_data = json.loads(blob_content)
                if body_data.get("format") == "jmap":
                    text_body = body_data.get("textBody", "")
                    html_body = body_data.get("htmlBody", "")
                else:
                    # Legacy format - treat as plain text
                    text_body = blob_content.decode("utf-8")
            except (json.JSONDecodeError, UnicodeDecodeError):
                text_body = message.draft_blob.get_content().decode(
                    "utf-8", errors="replace"
                )

        # Prepare and queue the message for sending
        prepare_outbound_message(
            mailbox_sender=mailbox,
            message=message,
            text_body=text_body,
            html_body=html_body,
            user=self.context.user,
        )

        # Queue async send task
        send_message_task.delay(str(message.id))

        return {
            "id": str(message.id),
            "emailId": str(message.id),
            "threadId": str(message.thread_id),
            "undoStatus": "final",
        }

    def _apply_on_success(
        self, email_id: str, creation_id: str, on_success_update: dict
    ) -> None:
        """Apply onSuccessUpdateEmail patches as an implicit Email/set response."""
        # Build the update map, replacing #emailSubmission/foo references with the email_id
        update_map = {}
        for ref_key, patch in on_success_update.items():
            # ref_key is like "#emailSubmission/creation_id"
            update_map[email_id] = patch

        # Execute the implicit Email/set
        implicit_args = {
            "accountId": str(self.context.user.id),
            "update": update_map,
        }
        handler = EmailSetMethod(self.context)
        result = handler.execute(implicit_args)

        # Add to implicit responses
        call_id = self.context.current_call_id
        self.context.implicit_responses.append(
            ["Email/set", result, call_id]
        )


# ---------- Identity Methods ----------


@MethodRegistry.register("Identity/get")
class IdentityGetMethod(BaseMethod):
    """Get sending identities (user's mailboxes)."""

    def execute(self, args: dict) -> dict:
        account_id = self._get_account_id(args)
        ids = args.get("ids")

        mailboxes = models.Mailbox.objects.filter(
            accesses__user=self.context.user
        ).select_related("domain")

        if ids is not None:
            mailboxes = mailboxes.filter(id__in=ids)

        result_list = []
        for mailbox in mailboxes:
            full_email = f"{mailbox.local_part}@{mailbox.domain.name}"
            result_list.append({
                "id": str(mailbox.id),
                "name": full_email,
                "email": full_email,
                "replyTo": None,
                "mayDelete": False,
            })

        found_ids = {str(m.id) for m in mailboxes}
        not_found = [mid for mid in (ids or []) if mid not in found_ids]

        return {
            "accountId": account_id,
            "state": self._get_state(),
            "list": result_list,
            "notFound": not_found,
        }
