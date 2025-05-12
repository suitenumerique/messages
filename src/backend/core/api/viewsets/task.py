"""API ViewSet for Celery task status."""

import logging

from celery.result import AsyncResult
from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema,
    inline_serializer,
)
from rest_framework import exceptions as drf_exceptions
from rest_framework import permissions
from rest_framework import serializers as drf_serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from messages.celery_app import app as celery_app

logger = logging.getLogger(__name__)


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
                "status": drf_serializers.CharField(),
                "result": drf_serializers.JSONField(allow_null=True),
                "error": drf_serializers.CharField(allow_null=True),
            },
        ),
        404: OpenApiExample("Not Found", value={"detail": "Task not found"}),
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
    """View to retrieve the status of a Celery task."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, task_id):
        """Get the status of a Celery task."""

        # Check if the task exists
        task_result = AsyncResult(task_id, app=celery_app)
        if not task_result.id:
            raise drf_exceptions.NotFound("Task not found")

        # Prepare the response data
        result_data = {
            "status": task_result.status,
            "result": None,
            "error": None,
        }

        # Include result or error information if available
        if task_result.successful():
            result_data["result"] = task_result.result
        elif task_result.failed():
            result_data["status"] = "FAILURE"
            result_data["error"] = str(task_result.result)
        elif task_result.state == "PROGRESS" and task_result.info:
            result_data.update(task_result.info)

        return Response(result_data)
