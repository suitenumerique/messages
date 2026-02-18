"""Reusable viewset mixins."""

from rest_framework import status
from rest_framework.response import Response

from core.api.serializers import ReadMessageTemplateSerializer


class MessageTemplateResponseMixin:
    """
    Mixin that returns ReadMessageTemplateSerializer
    for read, create and update responses.

    Viewsets using this mixin must define their own write `serializer_class`
    (e.g. `MessageTemplateSerializer`).  The mixin takes care of:

    * Switching to `ReadMessageTemplateSerializer` for *list* and *retrieve*.
    * Re-serialising the instance through the read serializer after *create*
      and *update* so that the response never leaks write-only fields.
    """

    def get_serializer_class(self):
        """Use the read serializer for list and retrieve actions."""
        if self.action in ("list", "retrieve"):
            return ReadMessageTemplateSerializer
        return super().get_serializer_class()

    def create(self, request, *args, **kwargs):
        """Create a template and return a read-serialized response."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        response_serializer = ReadMessageTemplateSerializer(
            serializer.instance, context=self.get_serializer_context()
        )
        headers = self.get_success_headers(response_serializer.data)
        return Response(
            response_serializer.data, status=status.HTTP_201_CREATED, headers=headers
        )

    def update(self, request, *args, **kwargs):
        """Update a template and return a read-serialized response."""
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        response_serializer = ReadMessageTemplateSerializer(
            serializer.instance, context=self.get_serializer_context()
        )
        return Response(response_serializer.data)
