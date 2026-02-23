"""Messages module."""

import os

# Ensure django-configurations is installed before settings are imported.
# This is needed because dramatiq worker processes import messages.settings
# via django_dramatiq.setup → django.setup(), and install() must be called
# before the Configuration metaclass runs.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "messages.settings")
os.environ.setdefault("DJANGO_CONFIGURATION", "Development")

from configurations.importer import install  # noqa: E402

install(check_options=True)
