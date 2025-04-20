"""Permission handlers for the messages core app."""

from django.core import exceptions

from rest_framework import permissions

from core import models

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


class IsAllowedToAccessMailbox(IsAuthenticated):
    """Permission class for access to a mailbox."""

    def has_permission(self, request, view):
        """Check if user has permission to access the mailbox thread list or message list."""
        # This check is primarily for LIST actions based on query params
        mailbox_id = request.query_params.get("mailbox_id")
        thread_id = request.query_params.get("thread_id")

        # If it's a detail action (retrieve, update, destroy), object-level permission is checked
        # by has_object_permission. If it's a list action without filters, deny access.
        if view.action != "list":
            # Allow the view to proceed to object-level checks for detail actions
            # or handle custom actions appropriately.
            return True

        # For LIST action, require either mailbox_id or thread_id
        if not mailbox_id and not thread_id:
            return False

        # Check access based on query params for LIST action
        if mailbox_id:
            return models.Mailbox.objects.filter(
                id=mailbox_id, accesses__user=request.user
            ).exists()
        if thread_id:
            return models.Thread.objects.filter(
                id=thread_id, mailbox__accesses__user=request.user
            ).exists()

        return False  # Should not be reached if logic above is correct

    def has_object_permission(self, request, view, obj):
        """Check if user has permission to access the specific object (Message, Thread, Mailbox)."""
        user = request.user

        if isinstance(obj, models.Message):
            # Check access via the message's thread's mailbox
            return models.MailboxAccess.objects.filter(
                mailbox=obj.thread.mailbox, user=user
            ).exists()
        if isinstance(obj, models.Thread):
            # Check access via the thread's mailbox
            return models.MailboxAccess.objects.filter(
                mailbox=obj.mailbox, user=user
            ).exists()
        if isinstance(obj, models.Mailbox):
            # Check access directly on the mailbox
            return models.MailboxAccess.objects.filter(mailbox=obj, user=user).exists()

        # Deny access for other object types or if type is unknown
        return False
