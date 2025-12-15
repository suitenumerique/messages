# pylint: disable=wildcard-import, unused-wildcard-import
"""Register all tasks here so that Celery autodiscovery can find them."""

from core.mda.inbound_tasks import *  # noqa: F403
from core.mda.outbound_tasks import *  # noqa: F403
from core.services.dns.tasks import *  # noqa: F403
from core.services.importer.tasks import *  # noqa: F403
from core.services.search.tasks import *  # noqa: F403
