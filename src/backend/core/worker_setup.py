"""Worker setup module for Dramatiq workers.

This module configures Django and imports configurations for worker processes.
"""

import django

from configurations.importer import install

install(check_options=True)
django.setup()
