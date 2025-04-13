"""
WSGI config for the messages project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/wsgi/
"""

import os

from configurations.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "messages.settings")
os.environ.setdefault("DJANGO_CONFIGURATION", "Development")

application = get_wsgi_application()
