"""API ViewSet for Contact model."""

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import exceptions, mixins, status, viewsets
from rest_framework.response import Response

from core import enums, models

from .. import permissions, serializers


class ContactViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
):
    """ViewSet for Contact model."""

    serializer_class = serializers.ContactSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        """Restrict results to contacts of mailboxes the current user has access to."""
        user_mailbox_ids = self.request.user.mailbox_accesses.values_list(
            "mailbox_id", flat=True
        )
        return models.Contact.objects.filter(
            mailbox_id__in=user_mailbox_ids
        ).select_related("mailbox", "mailbox__domain")

    @extend_schema(
        tags=["contacts"],
        parameters=[
            OpenApiParameter(
                name="mailbox_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description="Filter contacts by mailbox ID.",
                required=False,
            ),
            OpenApiParameter(
                name="q",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search contacts by name or email (case insensitive).",
                required=False,
            ),
        ],
        responses=serializers.ContactSuggestionSerializer(many=True),
    )
    def list(self, request, *args, **kwargs):
        """
        List contacts with optional filtering by mailbox and search query.
        For a mailbox, it returns the contacts the mailbox sent a message to
        and all contacts in the same domain as the mailbox. Contacts removed
        from the suggestions are left out.

        Query parameters:
        - mailbox_id: Optional UUID to filter contacts by mailbox
        - q: Optional search query for name or email (case insensitive)
        """
        # base queryset will be used to store contacts of the selected mailbox.
        # A contact is created for every address a received message carries
        # (spam, newsletters, people in copy), so only the ones the mailbox
        # wrote to are worth suggesting.
        queryset = self.get_queryset().filter(
            Exists(
                models.MessageRecipient.objects.filter(
                    contact=OuterRef("pk"),
                    message__is_sender=True,
                    message__is_draft=False,
                )
            ),
            is_suggested=True,
        )

        # extended_queryset will be used to store contacts from the same domain
        # as the selected mailbox
        extended_queryset = models.Contact.objects.none()

        # filter by search query on name and email (multi-word)
        search_query = request.query_params.get("q", "")
        queryset = (
            self.filter_by_search(queryset, search_query) if search_query else queryset
        )

        # Filter by mailbox if specified
        if mailbox_id := request.query_params.get("mailbox_id"):
            # check if the current user has access to the selected mailbox
            try:
                mailbox_access = (
                    self.request.user.mailbox_accesses.filter(mailbox_id=mailbox_id)
                    .select_related("mailbox", "mailbox__domain")
                    .get()
                )
            except models.MailboxAccess.DoesNotExist:
                return Response(
                    {"detail": "Invalid mailbox_id or access denied."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            # get all contacts of the selected mailbox
            queryset = queryset.filter(mailbox=mailbox_access.mailbox)

            # Hiding only applies to the mailbox owning the contact, so a
            # contact listed through the domain could not leave this list.
            hideable_mailbox_ids = (
                {mailbox_access.mailbox_id}
                if mailbox_access.role in enums.MAILBOX_ROLES_CAN_EDIT
                else set()
            )

            # get all contacts with same domain as the selected mailbox
            domain_contacts_ids = models.Mailbox.objects.filter(
                domain=mailbox_access.mailbox.domain
            ).values_list("contact__id", flat=True)
            extended_queryset = models.Contact.objects.filter(
                id__in=domain_contacts_ids
            ).select_related("mailbox", "mailbox__domain")

            # some contacts from the same domain may appear in the contacts
            # of the selected mailbox, so we need to exclude them
            extended_queryset = extended_queryset.exclude(
                email__in=queryset.values_list("email", flat=True)
            )
            # an address hidden by the selected mailbox must not come back
            # through the contact of the mailbox holding it in the same domain
            extended_queryset = extended_queryset.exclude(
                email__in=models.Contact.objects.filter(
                    mailbox=mailbox_access.mailbox, is_suggested=False
                ).values_list("email", flat=True)
            )
            # apply search filter to extended queryset
            if search_query:
                extended_queryset = self.filter_by_search(
                    extended_queryset, search_query
                )
        else:
            hideable_mailbox_ids = set(
                self.request.user.mailbox_accesses.filter(
                    role__in=enums.MAILBOX_ROLES_CAN_EDIT
                ).values_list("mailbox_id", flat=True)
            )

        # finally merge base and extended querysets
        queryset = queryset.union(extended_queryset).order_by("name", "email")
        serializer = serializers.ContactSuggestionSerializer(
            queryset,
            many=True,
            context={
                **self.get_serializer_context(),
                "hideable_mailbox_ids": hideable_mailbox_ids,
            },
        )
        return Response(serializer.data)

    @extend_schema(tags=["contacts"], responses={204: None})
    def destroy(self, request, *args, **kwargs):
        """
        Remove a contact from the recipient suggestions of its mailbox.

        The contact itself is kept, as the messages it sent or received still
        reference it. It is suggested again once a message is sent to it.
        """
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        """Hide the contact, provided the user can edit its mailbox."""
        if not self.request.user.mailbox_accesses.filter(
            mailbox_id=instance.mailbox_id, role__in=enums.MAILBOX_ROLES_CAN_EDIT
        ).exists():
            raise exceptions.PermissionDenied()

        # Bypasses the full_clean() of save(): a contact stored before the
        # address validator tightened would fail it, and these are precisely
        # the ones a user wants to get rid of.
        models.Contact.objects.filter(pk=instance.pk).update(
            is_suggested=False, updated_at=timezone.now()
        )

    def filter_by_search(self, queryset, search_query):
        """Search contacts by name or email (case insensitive)."""
        search_words = search_query.strip().split()
        if search_words:
            search_filters = Q()
            for word in search_words:
                word_filter = Q(name__unaccent__icontains=word) | Q(
                    email__unaccent__icontains=word
                )
                search_filters &= word_filter
            queryset = queryset.filter(search_filters)
        return queryset
