"""Root utils for the core application."""

import json
import time
from typing import Any, Dict, Optional

from django.core.cache import cache

from configurations import values
from dramatiq import actor
from dramatiq.middleware import CurrentMessage


def register_task(func):
    """Register a function as a Dramatiq task with result storage enabled.

    Args:
        func: The function to register as a task

    Returns:
        The decorated function as a Dramatiq actor
    """
    return actor(store_results=True)(func)


class JSONValue(values.Value):
    """
    A custom value class based on django-configurations Value class that
    allows to load a JSON string and use it as a value.
    """

    def to_python(self, value):
        """
        Return the python representation of the JSON string.
        """
        return json.loads(value)


def set_task_progress(progress: int, metadata: Optional[Dict[str, Any]] = None) -> None:
    """Set task progress in cache.

    Args:
        progress: Progress percentage (0-100)
        metadata: Optional metadata dictionary
    """
    # Get the current message ID from Dramatiq CurrentMessage middleware
    current_message = CurrentMessage.get_current_message()
    if not current_message:
        return  # Do nothing if no current message

    task_id = current_message.message_id
    progress_data = {
        "progress": progress,
        "timestamp": time.time(),
        "metadata": metadata or {},
    }
    cache.set(f"task_progress:{task_id}", progress_data, timeout=86400)  # 24 hours


def get_task_progress(task_id: str) -> Optional[Dict[str, Any]]:
    """Get task progress from cache.

    Args:
        task_id: Task identifier

    Returns:
        Progress data or None if not found
    """
    return cache.get(f"task_progress:{task_id}")
