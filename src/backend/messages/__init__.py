"""Messages module."""

import os

# django-configurations must be installed before ``messages.settings`` is
# imported, because the Configuration metaclass runs at class-definition time.
# ``manage.py`` and ``worker.py`` both do this themselves, but the Dramatiq CLI
# reaches settings through ``django.setup()`` in a re-executed process (module
# auto-reload, ``--watch``), where nothing else has had a chance to.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "messages.settings")
os.environ.setdefault("DJANGO_CONFIGURATION", "Development")

from configurations.importer import install  # pylint: disable=wrong-import-position

install(check_options=True)
