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
        """Check if user has permission to access the mailbox thread list or message list.
        Only role VIEWER is required to access the mailbox threads/messages.
        So we just need to check if user has any access role on mailbox or thread.
        """

        if not IsAuthenticated.has_permission(self, request, view):
            return False

        # This check is primarily for LIST actions based on query params
        mailbox_id = request.query_params.get("mailbox_id")  # Used by Thread list
        thread_id = request.query_params.get("thread_id")  # Used by Message list

        # If it's a detail action (retrieve, update, destroy), object-level permission is checked
        # by has_object_permission. If it's a list action without filters, deny access.
        is_list_action = hasattr(view, "action") and view.action == "list"

        if not is_list_action:
            # Allow non-list actions (like detail views or specific APIViews like SendMessageView)
            # to proceed to object-level checks or handle permissions within the view.
            return True

        # --- The following logic only applies if is_list_action is True --- #
        # Check access based on query params for LIST action
        if mailbox_id:
            # Check if the user has access to this specific mailbox to list threads
            return models.Mailbox.objects.filter(
                id=mailbox_id, accesses__user=request.user
            ).exists()
        if thread_id:
            # Check if the user has access to this specific thread to list messages
            return models.ThreadAccess.objects.filter(
                thread_id=thread_id, mailbox__accesses__user=request.user
            ).exists()

        return False  # Should not be reached if logic above is correct

    def has_object_permission(self, request, view, obj):
        """Check if user has permission to access the specific object (Message, Thread, Mailbox)."""
        user = request.user
        if isinstance(obj, models.Mailbox):
            # Check access directly on the mailbox
            return models.MailboxAccess.objects.filter(mailbox=obj, user=user).exists()

        if isinstance(obj, (models.Message, models.Thread)):
            thread = obj.thread if isinstance(obj, models.Message) else obj
            # Check access via the message's thread using ThreadAccess
            # First, just check if *any* access exists for the user to this thread.
            has_access = models.ThreadAccess.objects.filter(
                thread=thread, mailbox__accesses__user=user
            ).exists()
            if not has_access:
                return False

            # Only EDITOR or ADMIN role can destroy or send
            if view.action in ["destroy", "send"]:
                mailbox = thread.accesses.get(mailbox__accesses__user=user).mailbox
                if (
                    models.ThreadAccess.objects.filter(
                        thread=thread,
                        mailbox=mailbox,
                        role=enums.ThreadAccessRoleChoices.EDITOR,
                    ).exists()
                    and models.MailboxAccess.objects.filter(
                        mailbox=mailbox,
                        user=user,
                        role__in=[
                            enums.MailboxRoleChoices.EDITOR,
                            enums.MailboxRoleChoices.ADMIN,
                        ],
                    ).exists()
                ):
                    return True
            # for retrieve action has_access is already checked above
            else:
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

        # Check if user has required role on the sender Mailbox
        has_edit_role = view.mailbox.accesses.filter(
            user=request.user,
            role__in=[enums.MailboxRoleChoices.EDITOR, enums.MailboxRoleChoices.ADMIN],
        ).exists()

        # if user does not have edit role with this sender mailbox, return False
        if not has_edit_role:
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


class IsAllowedToManageThreadAccess(IsAuthenticated):
    """Permission class for access to create, update, delete and list thread accesses."""

    def has_permission(self, request, view):
        # Get thread_id from URL kwargs instead of query params/data
        thread_id = view.kwargs.get("thread_id")
        if not thread_id:
            return False

        # if create action, check if user has admin/editor access to the mailbox and the thread access role is editor
        if view.action == "create":
            # authenticated user wants to create a thread access for a specific thread
            # check if user has admin/editor access to the mailbox and the
            # thread access role is editor already exists for them
            return (
                models.ThreadAccess.objects.select_related("mailbox")
                .filter(
                    thread_id=thread_id,
                    mailbox__accesses__user=request.user,
                    mailbox__accesses__role__in=[
                        enums.MailboxRoleChoices.ADMIN,
                        enums.MailboxRoleChoices.EDITOR,
                    ],
                    role=enums.ThreadAccessRoleChoices.EDITOR,
                )
                .exists()
            )
        if view.action == "list":
            # list is only allowed for a user with access to the thread
            return (
                models.ThreadAccess.objects.select_related("mailbox")
                .filter(
                    thread_id=thread_id,
                    mailbox__accesses__user=request.user,
                    mailbox__accesses__role__in=[
                        enums.MailboxRoleChoices.ADMIN,
                        enums.MailboxRoleChoices.EDITOR,
                    ],
                    role=enums.ThreadAccessRoleChoices.EDITOR,
                )
                .exists()
            )

        return True  # to proceed to object-level checks

    def has_object_permission(self, request, view, obj):
        """Check if user has permission to access the specific object (ThreadAccess).
        Manage retrieve, update, destroy actions here.
        """
        # Verify the thread access belongs to the thread in the URL
        if obj.thread.id != view.kwargs.get("thread_id"):
            return False

        return (
            models.ThreadAccess.objects.select_related("mailbox")
            .filter(
                thread=obj.thread,
                mailbox__accesses__user=request.user,
                mailbox__accesses__role__in=[
                    enums.MailboxRoleChoices.ADMIN,
                    enums.MailboxRoleChoices.EDITOR,
                ],
                role=enums.ThreadAccessRoleChoices.EDITOR,
            )
            .exists()
        )


class IsMailDomainAdmin(permissions.BasePermission):
    """
    Allows access only to users who have ADMIN MailDomainAccess
    to the maildomain specified by 'maildomain_pk' in the URL.
    Used for viewsets nested under a maildomain.
    """

    message = "You do not have administrative rights for this mail domain."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        maildomain_pk = view.kwargs.get("maildomain_pk")
        if not maildomain_pk:
            return False

        return models.MailDomainAccess.objects.filter(
            user=request.user,
            maildomain_id=maildomain_pk,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        ).exists()

    # No has_object_permission, assumes objects are correctly scoped by view's get_queryset
    # based on the maildomain_pk.


class IsMailboxAdmin(permissions.BasePermission):
    """
    Allows access if the user has ADMIN MailboxAccess to the specific Mailbox
    identified by `view.kwargs['mailbox_id']`, OR if the user has ADMIN
    MailDomainAccess to the domain of that Mailbox.
    """

    message = "You do not have administrative rights for this mailbox or its domain."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        user = request.user
        mailbox_id_from_url = view.kwargs.get("mailbox_id")
        if not mailbox_id_from_url:
            return False  # Should not happen with correct URL configuration

        try:
            target_mailbox = models.Mailbox.objects.select_related("domain").get(
                pk=mailbox_id_from_url
            )
        except (models.Mailbox.DoesNotExist, ValueError):  # ValueError for invalid UUID
            return False

        # Check 1: Is user an admin of the specific mailbox?
        is_mailbox_admin = models.MailboxAccess.objects.filter(
            user=user, mailbox=target_mailbox, role=models.MailboxRoleChoices.ADMIN
        ).exists()

        if is_mailbox_admin:
            return True

        # Check 2: Is user an admin of the mailbox's domain?
        if target_mailbox.domain:
            is_domain_admin = models.MailDomainAccess.objects.filter(
                user=user,
                maildomain=target_mailbox.domain,
                role=models.MailDomainAccessRoleChoices.ADMIN,
            ).exists()
            if is_domain_admin:
                return True

        return False

    def has_object_permission(self, request, view, obj):
        # obj is a MailboxAccess instance.
        if not request.user or not request.user.is_authenticated:
            return False

        if not hasattr(obj, "mailbox") or not obj.mailbox or not obj.mailbox.domain:
            return False  # MailboxAccess must be linked to a Mailbox with a Domain

        # Ensure the object being acted upon belongs to the mailbox specified in the URL
        mailbox_id_from_url = view.kwargs.get("mailbox_id")
        if str(obj.mailbox.id) != str(mailbox_id_from_url):
            return False  # Object's mailbox does not match URL mailbox

        user = request.user
        target_mailbox = obj.mailbox  # The mailbox related to the MailboxAccess object

        # Check 1: Is user an admin of this specific mailbox?
        is_mailbox_admin = models.MailboxAccess.objects.filter(
            user=user, mailbox=target_mailbox, role=models.MailboxRoleChoices.ADMIN
        ).exists()

        if is_mailbox_admin:
            return True

        # Check 2: Is user an admin of the mailbox's domain?
        is_domain_admin = models.MailDomainAccess.objects.filter(
            user=user,
            maildomain=target_mailbox.domain,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        ).exists()

        return is_domain_admin
