"""Permission handlers for the messages core app."""

from django.core import exceptions

from rest_framework import permissions

from core import enums, models

ACTION_FOR_METHOD_TO_PERMISSION = {
    "versions_detail": {"DELETE": "versions_destroy", "GET": "versions_retrieve"},
    "children": {"GET": "children_list", "POST": "children_create"},
}


class IsAuthenticated(permissions.BasePermission):
    """
    Allows access only to authenticated users. Alternative method checking the presence
    of the auth token to avoid hitting the database.
    """

    def has_permission(self, request, view):
        return bool(request.auth) or request.user.is_authenticated


class IsAuthenticatedOrSafe(IsAuthenticated):
    """Allows access to authenticated users (or anonymous users but only on safe methods)."""

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return super().has_permission(request, view)


class IsSelf(IsAuthenticated):
    """
    Allows access only to authenticated users. Alternative method checking the presence
    of the auth token to avoid hitting the database.
    """

    def has_object_permission(self, request, view, obj):
        """Write permissions are only allowed to the user itself."""
        return obj == request.user


class IsOwnedOrPublic(IsAuthenticated):
    """
    Allows access to authenticated users only for objects that are owned or not related
    to any user via the "owner" field.
    """

    def has_object_permission(self, request, view, obj):
        """Unsafe permissions are only allowed for the owner of the object."""
        if obj.owner == request.user:
            return True

        if request.method in permissions.SAFE_METHODS and obj.owner is None:
            return True

        try:
            return obj.user == request.user
        except exceptions.ObjectDoesNotExist:
            return False


class AccessPermission(permissions.BasePermission):
    """Permission class for access objects."""

    def has_permission(self, request, view):
        return request.user.is_authenticated or view.action != "create"

    def has_object_permission(self, request, view, obj):
        """Check permission for a given object."""
        abilities = obj.get_abilities(request.user)
        action = view.action
        try:
            action = ACTION_FOR_METHOD_TO_PERMISSION[view.action][request.method]
        except KeyError:
            pass
        return abilities.get(action, False)


class IsAllowedToAccess(IsAuthenticated):
    """Permission class for access to a mailbox context or specific threads/messages."""

    def has_permission(self, request, view):
        """Check if user has permission to access the mailbox thread list or message list."""

        if not IsAuthenticated.has_permission(self, request, view):
            return False

        # This check is primarily for LIST actions based on query params
        mailbox_id = request.query_params.get("mailbox_id")
        thread_id = request.query_params.get("thread_id")  # Used by Message list

        # If it's a detail action (retrieve, update, destroy), object-level permission is checked
        # by has_object_permission. If it's a list action without filters, deny access.
        # Check if view has 'action' attribute and if it's 'list'
        is_list_action = hasattr(view, "action") and view.action == "list"

        if not is_list_action:
            # Allow non-list actions (like detail views or specific APIViews like SendMessageView)
            # to proceed to object-level checks or handle permissions within the view.
            return True

        # --- The following logic only applies if is_list_action is True --- #
        # Check access based on query params for LIST action
        if mailbox_id:
            # Check if the user has access to this specific mailbox
            return models.Mailbox.objects.filter(
                id=mailbox_id, accesses__user=request.user
            ).exists()
        if thread_id:
            # Check if the user has access to this specific thread
            return models.ThreadAccess.objects.filter(
                thread_id=thread_id, mailbox__accesses__user=request.user
            ).exists()

        return False  # Should not be reached if logic above is correct

    def has_object_permission(self, request, view, obj):
        """Check if user has permission to access the specific object (Message, Thread, Mailbox)."""
        user = request.user
        if isinstance(obj, models.Mailbox):
            # Check access directly on the mailbox (e.g., for listing mailboxes)
            # This assumes MailboxAccess still exists for managing mailbox settings/sharing itself
            return models.MailboxAccess.objects.filter(mailbox=obj, user=user).exists()

        if isinstance(obj, (models.Message, models.Thread)):
            thread = obj.thread if isinstance(obj, models.Message) else obj
            # Check access via the message's thread using ThreadAccess
            # Specific permission levels (like DELETE) might be checked here or in the view
            # For now, just check if *any* access exists for the user to this thread.
            has_access = models.ThreadAccess.objects.filter(
                thread=thread, mailbox__accesses__user=user
            ).exists()
            if not has_access:
                return False

            if view.action in ["destroy", "send"]:
                permissions_required = (
                    [
                        enums.MailboxPermissionChoices.DELETE,
                        enums.MailboxPermissionChoices.ADMIN,
                    ]
                    if view.action == "destroy"
                    else [
                        enums.MailboxPermissionChoices.SEND,
                        enums.MailboxPermissionChoices.ADMIN,
                    ]
                )
                mailbox = thread.accesses.get(mailbox__accesses__user=user).mailbox
                # Delete and send permissions are only allowed for EDITOR role and some mailbox permissions
                if (
                    models.ThreadAccess.objects.filter(
                        thread=thread,
                        mailbox=mailbox,
                        role=enums.ThreadAccessRoleChoices.EDITOR,
                    ).exists()
                    and models.MailboxAccess.objects.filter(
                        mailbox=mailbox,
                        user=user,
                        permission__in=permissions_required,
                    ).exists()
                ):
                    return True
            else:
                # for retrieve action has_access is already checked below
                return True

        # Deny access for other object types or if type is unknown
        return False


class IsAllowedToCreateMessage(IsAuthenticated):
    """Permission class for access to create a message."""

    def has_permission(self, request, view):
        """Check if user is allowed to create a message."""

        if not IsAuthenticated.has_permission(self, request, view):
            return False

        # a sender mailbox is required to create/send a message
        sender_id = request.data.get("senderId")
        parent_id = request.data.get("parentId")
        if not sender_id:
            return False

        # get mailbox instance from sender id
        try:
            # Store mailbox on the view for later use (e.g., in the view logic)
            view.mailbox = models.Mailbox.objects.get(id=sender_id)
        except models.Mailbox.DoesNotExist:
            return False  # Invalid senderId

        # Check if user has required permissions on the sender Mailbox
        permissions_required = [
            enums.MailboxPermissionChoices.EDIT,
            enums.MailboxPermissionChoices.ADMIN,
        ]
        # check if user has access required to send a message with this mailbox
        has_edit_permission = view.mailbox.accesses.filter(
            user=request.user,
            permission__in=permissions_required,
        ).exists()

        # if user does not have edit permission with this sender mailbox, return False
        if not has_edit_permission:
            return False

        # --- Additional check for replies ---
        # If creating a reply (parentId is provided), check access to the parent thread
        if parent_id:
            try:
                parent_message = models.Message.objects.select_related("thread").get(
                    id=parent_id
                )
                # Check if the user has access to the thread they are replying to
                if models.ThreadAccess.objects.filter(
                    thread=parent_message.thread,
                    mailbox=view.mailbox,
                    role=models.ThreadAccessRoleChoices.EDITOR,
                ).exists():
                    return True
            except models.Message.DoesNotExist:
                return False  # Treat invalid parentId as permission failure

        # --- Additional check for updating existing draft ---
        # If updating (messageId is provided), check access to the draft's thread
        message_id = request.data.get("messageId")
        if message_id and request.method == "PUT":  # Check only needed for updates
            try:
                draft_message = models.Message.objects.select_related("thread").get(
                    id=message_id, is_draft=True
                )
                # Check if the user has access to the thread of the draft being updated
                if not models.ThreadAccess.objects.filter(
                    thread=draft_message.thread,
                    mailbox=view.mailbox,
                    role=models.ThreadAccessRoleChoices.EDITOR,
                ).exists():
                    return False
            except models.Message.DoesNotExist:
                # Let the view handle invalid messageId
                return False  # Treat invalid messageId as permission failure

        # If all checks pass
        return True


# class IsAllowedToSendMessage(IsAuthenticated):
#    """Permission class for access to send a message."""
#
#    def has_permission(self, request, view):
#        """Check if user is allowed to send a message."""
#        # a sender is required to create a message
#
#        if not IsAuthenticated.has_permission(self, request, view):
#            return False
#
#        sender_id = request.data.get("senderId")
#        if not sender_id:
#            return False
#        # get mailbox instance from sender id
#        try:
#            view.mailbox = models.Mailbox.objects.get(id=sender_id)
#        except models.Mailbox.DoesNotExist:
#            return False
#        # required permissions to send a message
#        permissions_required = [
#            enums.MailboxPermissionChoices.SEND,
#            enums.MailboxPermissionChoices.ADMIN,
#        ]
#        # check if user has access required to send a message with this mailbox
#        if not view.mailbox.accesses.filter(
#            user=request.user,
#            permission__in=permissions_required,
#        ).exists():
#            # user does not have permission to send a message with this mailbox
#            return False
#
#        # check if user has access to the thread
#        if models.ThreadAccess.objects.filter(
#            mailbox=view.mailbox,
#            thread=view.thread,
#            role=models.ThreadAccessRoleChoices.EDITOR,
#        ).exists():
#            return True
#
#        return False
