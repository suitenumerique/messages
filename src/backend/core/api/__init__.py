"""Messages core API endpoints"""

import logging

from django.conf import settings
from django.core.exceptions import ValidationError

from rest_framework import exceptions as drf_exceptions
from rest_framework import views as drf_views
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.services.throttle import ThrottleLimitExceeded

logger = logging.getLogger(__name__)


def exception_handler(exc, context):
    """Handle Django ValidationError and ThrottleLimitExceeded.

    For the parameters, see ``exception_handler``
    This code comes from twidi's gist:
    https://gist.github.com/twidi/9d55486c36b6a51bdcb05ce3a763e79f
    """
    if isinstance(exc, ThrottleLimitExceeded):
        logger.warning(
            "Throttle limit exceeded for %s: %s/%s (retry after %ds)",
            exc.entity_type,
            exc.current,
            exc.limit,
            exc.retry_after,
        )
        response = Response(
            {"detail": "Sending limit reached. Please wait before trying again."},
            status=429,
        )
        response["Retry-After"] = exc.retry_after
        return response

    if isinstance(exc, ValidationError):
        detail = None
        if hasattr(exc, "message_dict"):
            detail = exc.message_dict
        elif hasattr(exc, "message"):
            detail = exc.message
        elif hasattr(exc, "messages"):
            detail = exc.messages

        exc = drf_exceptions.ValidationError(detail=detail)

    return drf_views.exception_handler(exc, context)


# pylint: disable=unused-argument
@api_view(["GET"])
def get_frontend_configuration(request):
    """Returns the frontend configuration dict as configured in settings."""
    frontend_configuration = {
        "LANGUAGE_CODE": settings.LANGUAGE_CODE,
    }
    frontend_configuration.update(settings.FRONTEND_CONFIGURATION)
    return Response(frontend_configuration)
