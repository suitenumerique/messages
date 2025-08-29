"""
WSGI config for the messages project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/wsgi/
"""

import os

from configurations.wsgi import get_wsgi_application

# Remove access logs for healthcheck route
# pylint: disable=all
try:
    import django.core.servers.basehttp

    class QuietWSGIRequestHandler(django.core.servers.basehttp.WSGIRequestHandler):
        def log_message(self, format, *args):
            # # args[0] is the request line, e.g. 'GET /v1.0/healthz/ HTTP/1.1'
            if len(args) > 0 and "/v1.0/healthz" in args[0]:
                return
            super().log_message(format, *args)

    django.core.servers.basehttp.WSGIRequestHandler = QuietWSGIRequestHandler
except ImportError:
    pass
# pylint: enable=all

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "messages.settings")
os.environ.setdefault("DJANGO_CONFIGURATION", "Development")

application = get_wsgi_application()
