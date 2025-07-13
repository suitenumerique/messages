"""Messages celery configuration file."""

import os

from celery import Celery
from configurations.importer import install

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "messages.settings")
os.environ.setdefault("DJANGO_CONFIGURATION", "Development")

install(check_options=True)

app = Celery("messages")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# Configure beat schedule
app.conf.beat_schedule = {
    "retry-pending-messages": {
        "task": "core.tasks.retry_messages_task",
        "schedule": 300.0,  # Every 5 minutes (300 seconds)
        "options": {"queue": "default"},
    },
}
