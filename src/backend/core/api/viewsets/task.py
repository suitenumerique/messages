"""API ViewSet for background task status."""

import logging

import dramatiq
from dramatiq.results import ResultFailure, ResultMissing
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

from core.task_utils import get_task_progress, get_task_tracking

logger = logging.getLogger(__name__)

# The states this endpoint reports. Deliberately a small, stable set — clients
# poll until it leaves PENDING/PROGRESS.
TASK_STATES = ["PENDING", "PROGRESS", "SUCCESS", "FAILURE"]

#: Distinguishes "no result recorded" from a task that finished and returned
#: ``None`` — collapsing the two would report a completed task as PENDING for
#: as long as the client cared to poll.
MISSING = object()


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
                "status": drf_serializers.ChoiceField(choices=TASK_STATES),
                "result": drf_serializers.JSONField(allow_null=True),
                "error": drf_serializers.CharField(allow_null=True),
                # Only present while status == "PROGRESS".
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
    """View to retrieve the status of a background task."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, task_id):
        """Get the status of a background task.

        An unknown id is indistinguishable from an expired one — nothing keeps
        a record of a task that never existed — so both come back as
        PermissionDenied rather than leaking which ids are real.
        """
        tracking = get_task_tracking(task_id)
        if tracking is None:
            raise PermissionDenied("Task not found or access expired.")
        if str(request.user.id) != tracking["owner"]:
            raise PermissionDenied("You do not have access to this task.")

        result_data = self._get_result(task_id, tracking)
        if isinstance(result_data, Response):
            return result_data

        if result_data is not MISSING:
            # Tasks that need to report a failure without dead-lettering
            # return an explicit {status, result, error} envelope; pass it
            # through rather than wrapping it a second time.
            if (
                isinstance(result_data, dict)
                and {"status", "result", "error"} <= result_data.keys()
            ):
                return Response(
                    {
                        "status": result_data["status"],
                        "result": result_data["result"],
                        "error": result_data["error"],
                    }
                )
            return Response({"status": "SUCCESS", "result": result_data, "error": None})

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

        # Neither a result nor any progress: still queued, or running but
        # silent. There is no way to tell the two apart, and callers polling a
        # spinner don't need to.
        return Response({"status": "PENDING", "result": None, "error": None})

    def _get_result(self, task_id, tracking):
        """Read the stored return value, ``MISSING``, or a Response on failure.

        Results are keyed by (queue, actor, message id), so reading one back
        means rebuilding the message that produced it — which is why the
        tracking record carries the actor and queue names.
        """
        if not tracking.get("actor_name") or not tracking.get("queue_name"):
            # A pre-minted id whose task has not been dispatched yet: there is
            # nothing to look up, and PENDING is the right answer.
            return MISSING

        message = dramatiq.Message(
            queue_name=tracking["queue_name"],
            actor_name=tracking["actor_name"],
            args=(),
            kwargs={},
            options={},
            message_id=task_id,
        )
        try:
            return message.get_result(block=False)
        except ResultMissing:
            return MISSING
        except ResultFailure as exc:
            # The task raised and exhausted its retries. The exception message
            # is logged but not returned: it can carry internal hostnames,
            # credentials in URLs, or fragments of the payload.
            logger.error("Task %s failed: %s", task_id, exc)
            return Response(
                {"status": "FAILURE", "result": None, "error": "Task failed"}
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to read the result of task %s", task_id)
            return Response(
                {
                    "status": "FAILURE",
                    "result": None,
                    "error": "Result backend unavailable",
                },
                status=503,
            )
