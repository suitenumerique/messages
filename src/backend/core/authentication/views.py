"""Mobile-aware OIDC views handing the Django session over to the Capacitor apps.

The mobile apps run the OIDC flow in the system browser (ASWebAuthenticationSession
on iOS, Chrome Custom Tabs on Android) so the identity provider session cookie is
shared across apps and provides cross-app SSO. The backend remains the confidential
OIDC client: once the callback has created the Django session, it redirects the
system browser to the app deep link with a one-time token that the app exchanges
for the session cookie (see `core.api.viewsets.mobile_auth`).
"""

import logging
import secrets
import time
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import SuspiciousOperation
from django.core.handlers.wsgi import WSGIRequest
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect

from lasuite.oidc_login.views import (
    OIDCAuthenticationCallbackView as LaSuiteOIDCAuthenticationCallbackView,
)
from lasuite.oidc_login.views import (
    OIDCAuthenticationRequestView as LaSuiteOIDCAuthenticationRequestView,
)

logger = logging.getLogger(__name__)

MOBILE_AUTH_SESSION_KEY = "mobile_auth"
MOBILE_AUTH_TOKEN_CACHE_PREFIX = "mobile-auth-token"  # noqa: S105 (cache key prefix, not a secret)
# A pending mobile login older than this is ignored by the callback, so an
# abandoned mobile attempt cannot turn a later web login performed in the same
# browser session into a deep-link redirect.
MOBILE_AUTH_FLAG_MAX_AGE = 60 * 10


class AppSchemeRedirect(HttpResponseRedirect):
    """Redirect response whose allowed schemes are the configured mobile app schemes."""

    def __init__(self, redirect_to: str, *args, **kwargs) -> None:
        """Allow the configured mobile schemes before validating the redirect URL."""
        self.allowed_schemes = list(settings.MOBILE_AUTH_CALLBACK_SCHEMES)
        super().__init__(redirect_to, *args, **kwargs)


class OIDCAuthenticationRequestView(LaSuiteOIDCAuthenticationRequestView):
    """Authentication request view supporting a mobile session handoff.

    Mobile apps add `mobile_scheme` (an allowlisted deep-link scheme) and
    `code_challenge` (PKCE S256) to the authenticate URL. The pair is stored in
    the session so the callback view knows where to hand the session over.
    """

    def get(self, request: WSGIRequest) -> HttpResponse:
        """Flag the session when the login is initiated by a mobile app."""
        mobile_scheme = request.GET.get("mobile_scheme", "")
        if mobile_scheme:
            if mobile_scheme not in settings.MOBILE_AUTH_CALLBACK_SCHEMES:
                raise SuspiciousOperation("Unknown mobile callback scheme.")
            code_challenge = request.GET.get("code_challenge", "")
            if not code_challenge:
                return HttpResponseBadRequest("Missing code_challenge.")
            request.session[MOBILE_AUTH_SESSION_KEY] = {
                "scheme": mobile_scheme,
                "code_challenge": code_challenge,
                "created_at": time.time(),
            }
            # The parent view forces a session save before redirecting to the
            # identity provider, so the flag survives the OIDC round-trip.
        return super().get(request)


class OIDCAuthenticationCallbackView(LaSuiteOIDCAuthenticationCallbackView):
    """Callback view handing the session over to the mobile app via deep link."""

    def _pop_mobile_auth(self) -> dict | None:
        """Pop and return the pending mobile login data, if any and still fresh."""
        mobile_auth = self.request.session.pop(MOBILE_AUTH_SESSION_KEY, None)
        if mobile_auth is None:
            return None
        self.request.session.save()
        if time.time() - mobile_auth["created_at"] > MOBILE_AUTH_FLAG_MAX_AGE:
            logger.warning("Ignoring stale mobile login flag.")
            return None
        return mobile_auth

    def login_success(self) -> HttpResponse:
        """Redirect to the mobile app with a one-time token after a mobile login."""
        response = super().login_success()
        mobile_auth = self._pop_mobile_auth()
        if mobile_auth is None:
            return response

        token = secrets.token_urlsafe(32)
        cache.set(
            f"{MOBILE_AUTH_TOKEN_CACHE_PREFIX}:{token}",
            {
                # auth.login() cycled the session key, hence read after super().
                "session_key": self.request.session.session_key,
                "code_challenge": mobile_auth["code_challenge"],
            },
            timeout=settings.MOBILE_AUTH_TOKEN_TTL,
        )
        query = urlencode({"token": token})
        return AppSchemeRedirect(f"{mobile_auth['scheme']}://auth?{query}")

    def login_failure(self) -> HttpResponse:
        """Redirect to the mobile app with an error after a failed mobile login."""
        response = super().login_failure()
        mobile_auth = self._pop_mobile_auth()
        if mobile_auth is None:
            return response
        return AppSchemeRedirect(f"{mobile_auth['scheme']}://auth?error=login_failed")
