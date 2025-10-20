"""API ViewSet for asynchronous task statuses."""

import logging

import dramatiq
from dramatiq.results import ResultFailure, ResultMissing, ResultTimeout
from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema,
    inline_serializer,
)
from rest_framework import permissions
from rest_framework import serializers as drf_serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from core.utils import get_task_progress

logger = logging.getLogger(__name__)

# Map Dramatiq states to Celery-like states for frontend compatibility
DRAMATIQ_STATES = ["PENDING", "SUCCESS", "FAILURE", "RETRY", "REJECTED", "PROGRESS"]


@extend_schema(
    tags=["tasks"],
    parameters=[
        {
            "name": "task_id",
            "in": "path",
            "required": True,
            "description": "Task ID",
            "schema": {"type": "string"},
        }
    ],
    responses={
        200: inline_serializer(
            name="TaskStatusResponse",
            fields={
                "status": drf_serializers.ChoiceField(choices=sorted(DRAMATIQ_STATES)),
                "result": drf_serializers.JSONField(allow_null=True),
                "error": drf_serializers.CharField(allow_null=True),
            },
        )
    },
    description="""
    Get the status of an async task.

    This endpoint returns the current status of a task identified by its ID.
    """,
    examples=[
        OpenApiExample(
            "Task Status",
            value={
                "status": "SUCCESS",
                "result": {"success": True},
                "error": None,
            },
        ),
    ],
)
class TaskDetailView(APIView):
    """View to retrieve the status of a task."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, task_id):
        """Get the status of a task."""
        # Try to fetch a result from Dramatiq's result backend
        broker = dramatiq.get_broker()
        result_backend = broker.get_results_backend()

        if result_backend is not None:
            try:
                # Retrieve a Message for this task id from the backend, then get result
                # See Dramatiq results API: message.get_result(...)
                message = result_backend.get_message(task_id)
                result = message.get_result(backend=result_backend, block=False)
                return Response(
                    {
                        "status": "SUCCESS",
                        "result": result,
                        "error": None,
                    }
                )
            except ResultMissing:
                # No result yet; fall through to progress/pending logic
                pass
            except ResultFailure as exc:
                return Response(
                    {
                        "status": "FAILURE",
                        "result": None,
                        "error": str(exc),
                    }
                )
            except ResultTimeout as exc:
                # Treat timeouts as pending
                logger.debug("Result timeout for task %s: %s", task_id, exc)

        # Check if we have progress data for this task
        progress_data = get_task_progress(task_id)

        if progress_data:
            # Task is in progress
            return Response(
                {
                    "status": "PROGRESS",
                    "result": None,
                    "error": None,
                    "progress": progress_data.get("progress"),
                    "message": progress_data.get("metadata", {}).get("message"),
                    "timestamp": progress_data.get("timestamp"),
                }
            )

        # Default to pending when no result and no progress
        return Response(
            {
                "status": "PENDING",
                "result": None,
                "error": None,
            }
        )
