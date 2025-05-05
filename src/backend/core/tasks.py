"""Core tasks."""

from celery.utils.log import get_task_logger

from core import models
from core.mda.outbound import send_message

from messages.celery_app import app as celery_app

logger = get_task_logger(__name__)


@celery_app.task(bind=True)
def send_message_task(self, message_id, force_mta_out=False):
    """Send a message asynchronously.

    Args:
        message_id: The ID of the message to send
        mime_data: The MIME data dictionary
        force_mta_out: Whether to force sending via MTA

    Returns:
        dict: A dictionary with recipient emails as keys and success status as values
    """
    try:
        message = (
            models.Message.objects.select_related("thread", "sender")
            .prefetch_related("recipients__contact")
            .get(id=message_id)
        )

        send_message(message, force_mta_out)

        # Update task state with progress information
        self.update_state(
            state="SUCCESS",
            meta={
                "status": "completed",  # TODO fetch recipients statuses
                "message_id": str(message_id),
                "success": True,
            },
        )

        return {
            "task_id": self.request.id,
            "message_id": str(message_id),
            "success": True,
        }
    except Exception as e:
        logger.exception("Error in send_message_task for message %s: %s", message_id, e)
        self.update_state(
            state="FAILURE",
            meta={"status": "failed", "message_id": str(message_id), "error": str(e)},
        )
        raise
