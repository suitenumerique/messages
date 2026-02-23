"""API ViewSet for asynchronous task statuses."""

import logging

import dramatiq
from dramatiq.results import ResultFailure, ResultMissing, Results

from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema,
    inline_serializer,
)
from rest_framework import permissions
from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from core.utils import get_task_progress, get_task_tracking

logger = logging.getLogger(__name__)


TASK_STATES = ["PENDING", "SUCCESS", "FAILURE", "PROGRESS"]


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
                "status": drf_serializers.ChoiceField(choices=sorted(TASK_STATES)),
                "result": drf_serializers.JSONField(allow_null=True),
                "error": drf_serializers.CharField(allow_null=True),
                # Present when status == "PROGRESS"
                "progress": drf_serializers.IntegerField(required=False),
                "message": drf_serializers.CharField(required=False, allow_blank=True),
                "timestamp": drf_serializers.FloatField(required=False),
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
        tracking = get_task_tracking(task_id)
        if tracking is None:
            raise PermissionDenied("Task not found or access expired.")
        if str(request.user.id) != tracking["owner"]:
            raise PermissionDenied("You do not have access to this task.")

        # Try to fetch the result from dramatiq's native result backend
        message = dramatiq.Message(
            queue_name=tracking["queue_name"],
            actor_name=tracking["actor_name"],
            args=(),
            kwargs={},
            options={},
            message_id=task_id,
        )
        try:
            result_data = message.get_result(block=False)
        except ResultMissing:
            result_data = None
        except ResultFailure as exc:
            return Response({
                "status": "FAILURE",
                "result": None,
                "error": str(exc),
            })

        if result_data is not None:
            response = {"status": "SUCCESS", "result": result_data, "error": None}
            # If the result follows the {status, result, error} convention, unpack it
            if (
                isinstance(result_data, dict)
                and {"status", "result", "error"} <= result_data.keys()
            ):
                response["status"] = result_data["status"]
                response["result"] = result_data["result"]
                response["error"] = result_data["error"]
            return Response(response)

        # Check if we have progress data for this task
        progress_data = get_task_progress(task_id)
        if progress_data:
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
