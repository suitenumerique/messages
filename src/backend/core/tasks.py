# pylint: disable=wildcard-import, unused-wildcard-import
"""Import every task module here so the worker and the scheduler find them.

``DRAMATIQ_AUTODISCOVER_MODULES = ["tasks"]`` makes this the one module both
``worker.py`` and ``manage.py crontab`` import per app, so a task that is not
reachable from here is never registered — it would silently never run.
"""

import logging

from django.conf import settings

from core.mda.dispatch_webhooks import (  # noqa: F401  # pylint: disable=unused-import
    dispatch_webhook_task,
)
from core.mda.inbound_tasks import *  # noqa: F403
from core.mda.outbound_tasks import *  # noqa: F403
from core.services.blob_gc import *  # noqa: F403
from core.services.calendar.tasks import *  # noqa: F403
from core.services.dns.tasks import *  # noqa: F403
from core.services.importer.tasks import *  # noqa: F403
from core.services.push.tasks import *  # noqa: F403
from core.services.search.tasks import *  # noqa: F403
from core.services.tiered_storage_tasks import *  # noqa: F403
from core.task_utils import cron_task, register_task

logger = logging.getLogger(__name__)


@cron_task(crontab="45 2 * * *")
@register_task(queue="default")
def prune_task_history_task():
    """Drop task-history rows older than ``TASK_HISTORY_MAX_AGE``.

    A no-op unless ``TASK_HISTORY_ENABLED`` is on — without it nothing writes
    that table in the first place. With it on, the table gains a row per
    dispatched task and would otherwise grow without bound.
    """
    if not settings.TASK_HISTORY_ENABLED:
        return {"success": True, "reason": "disabled"}

    # pylint: disable-next=import-outside-toplevel
    from django_dramatiq.models import Task

    Task.tasks.delete_old_tasks(settings.TASK_HISTORY_MAX_AGE)
    return {"success": True}
