"""Custom OIDC views: mobile session handoff and IdP-terminating logout.

The mobile apps run the OIDC flow in the system browser (ASWebAuthenticationSession
on iOS, Chrome Custom Tabs on Android) so the identity provider session cookie is
shared across apps and provides cross-app SSO. The backend remains the confidential
OIDC client: once the callback has created the Django session, it redirects the
system browser to the app deep link with a one-time token that the app exchanges
for the session cookie (see `core.api.viewsets.mobile_auth`).

The logout view terminates the IdP session even when the Django session is
anonymous — see `OIDCLogoutView`.
"""

import logging
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse

from django.conf import settings
from django.contrib import auth
from django.core.cache import cache
from django.core.exceptions import SuspiciousOperation
from django.core.handlers.wsgi import WSGIRequest
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import render

from lasuite.oidc_login.views import (
    OIDCAuthenticationCallbackView as LaSuiteOIDCAuthenticationCallbackView,
)
from lasuite.oidc_login.views import (
    OIDCAuthenticationRequestView as LaSuiteOIDCAuthenticationRequestView,
)
from lasuite.oidc_login.views import (
    OIDCLogoutCallbackView as LaSuiteOIDCLogoutCallbackView,
)
from lasuite.oidc_login.views import OIDCLogoutView as LaSuiteOIDCLogoutView

logger = logging.getLogger(__name__)

MOBILE_AUTH_SESSION_KEY = "mobile_auth"
MOBILE_LOGOUT_SESSION_KEY = "mobile_logout"
MOBILE_AUTH_TOKEN_CACHE_PREFIX = "mobile-auth-token"  # noqa: S105 (cache key prefix, not a secret)
# A pending mobile login/logout older than this is ignored by the callbacks,
# so an abandoned mobile attempt cannot turn a later web flow performed in the
# same browser session into a deep-link redirect.
MOBILE_AUTH_FLAG_MAX_AGE = 60 * 10


class AppSchemeRedirect(HttpResponseRedirect):
    """Redirect response whose allowed schemes are the configured mobile app schemes."""

    def __init__(self, redirect_to: str, *args, **kwargs) -> None:
        """Allow the configured mobile schemes before validating the redirect URL."""
        self.allowed_schemes = list(settings.MOBILE_AUTH_CALLBACK_SCHEMES)
        super().__init__(redirect_to, *args, **kwargs)


def mobile_login_handoff(request: WSGIRequest, app_url: str) -> HttpResponse:
    """Serve the page handing a finished mobile login back to the app deep link.

    A plain 302 to the custom scheme gets blocked by the Content Security
    Policy of the IdP login page: Chrome enforces its ``form-action`` on the
    whole redirect chain of the credential form submission, and ``*`` only
    matches network schemes — the final custom-scheme hop violates it and the
    sheet stays stuck on the IdP ("Sending form data … violates form-action").
    ProConnect sends such a CSP; the dev Keycloak does not, which hides the
    bug in dev, and the logout chain involves no form submission, so its
    direct scheme redirects are fine. Serving a 200 page ends the form chain
    on a network scheme; the deep link then leaves from *this* page, outside
    the IdP policy: automatically via script (iOS ASWebAuthenticationSession
    intercepts it; Android may still gate an external-protocol launch on user
    activation) with a tap target as the fallback that always works.
    """
    response = render(request, "core/mobile_handoff.html", {"app_url": app_url})
    # The page embeds a one-time bearer token: it must never outlive the
    # navigation that carried it.
    response["Cache-Control"] = "no-store"
    return response


def get_requested_mobile_scheme(request: WSGIRequest) -> str:
    """Return the validated `mobile_scheme` query parameter ("" when absent).

    The scheme is the deep-link destination of the whole flow: only
    allowlisted schemes may terminate it.
    """
    mobile_scheme = request.GET.get("mobile_scheme", "")
    if mobile_scheme and mobile_scheme not in settings.MOBILE_AUTH_CALLBACK_SCHEMES:
        raise SuspiciousOperation("Unknown mobile callback scheme.")
    return mobile_scheme


def pop_mobile_flag(session, key: str, state: str | None) -> dict | None:
    """Pop the pending mobile flow flag, if it belongs to `state` and is fresh.

    `state` identifies the OIDC round-trip the callback belongs to. A session
    can carry several valid states at once (overlapping or abandoned flows), so
    a flag consumed by the wrong callback would send that flow to the app deep
    link — a web login or logout landing on a custom scheme — and leave the
    mobile one without it.
    """
    mobile_flag = session.get(key)
    if mobile_flag is None:
        return None
    if not state or mobile_flag.get("state") != state:
        return None
    del session[key]
    session.save()
    if time.time() - mobile_flag["created_at"] > MOBILE_AUTH_FLAG_MAX_AGE:
        logger.warning("Ignoring stale %s flag.", key)
        return None
    return mobile_flag


class OIDCAuthenticationRequestView(LaSuiteOIDCAuthenticationRequestView):
    """Authentication request view supporting a mobile session handoff.

    Mobile apps add `mobile_scheme` (an allowlisted deep-link scheme) and
    `code_challenge` (PKCE S256) to the authenticate URL. The pair is stored in
    the session — bound to the state of this OIDC round-trip — so the callback
    view knows where to hand the session over.
    """

    def get(self, request: WSGIRequest) -> HttpResponse:
        """Flag the session when the login is initiated by a mobile app."""
        mobile_scheme = get_requested_mobile_scheme(request)
        if not mobile_scheme:
            return super().get(request)

        code_challenge = request.GET.get("code_challenge", "")
        if not code_challenge:
            return HttpResponseBadRequest("Missing code_challenge.")

        response = super().get(request)
        # Flagged after super(): the state the parent view just generated ties
        # the flag to this round-trip only. Its own session save happened
        # before, hence the explicit one below.
        request.session[MOBILE_AUTH_SESSION_KEY] = {
            "scheme": mobile_scheme,
            "code_challenge": code_challenge,
            "state": parse_qs(urlparse(response["Location"]).query)["state"][0],
            "created_at": time.time(),
        }
        request.session.save()
        return response


class OIDCAuthenticationCallbackView(LaSuiteOIDCAuthenticationCallbackView):
    """Callback view handing the session over to the mobile app via deep link."""

    def pop_mobile_auth(self) -> dict | None:
        """Return the mobile flag of the login this callback closes, if any.

        The parent view consumed the state from `oidc_states` before handing
        over, but the query parameter still identifies the round-trip: a
        callback carrying no state at all (a malformed one, never a real IdP
        answer) belongs to no known flow and must leave the flag alone.
        """
        return pop_mobile_flag(
            self.request.session,
            MOBILE_AUTH_SESSION_KEY,
            self.request.GET.get("state"),
        )

    def login_success(self) -> HttpResponse:
        """Hand a one-time session token to the mobile app after a mobile login."""
        response = super().login_success()
        mobile_auth = self.pop_mobile_auth()
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
        return mobile_login_handoff(
            self.request, f"{mobile_auth['scheme']}://auth?{query}"
        )

    def login_failure(self) -> HttpResponse:
        """Hand the login error back to the mobile app after a failed mobile login."""
        response = super().login_failure()
        mobile_auth = self.pop_mobile_auth()
        if mobile_auth is None:
            return response
        return mobile_login_handoff(
            self.request, f"{mobile_auth['scheme']}://auth?error=login_failed"
        )


class OIDCLogoutView(LaSuiteOIDCLogoutView):
    """Logout view triggering the IdP logout whatever the Django session state.

    The parent view only initiates the RP-initiated logout for authenticated
    users, but a sign-in that matched no Messages account leaves the Django
    session *unauthenticated* while the IdP session is still alive (the
    id_token is stored before user matching) — and that IdP session is exactly
    what keeps signing the same identity back in: ProConnect ignores
    ``prompt=login``, so terminating its session is the only way to let the
    user restart an authentication cycle with another identity.

    The trigger is therefore relaxed to "the session holds an id_token"; the
    rest of the parent flow is untouched (post_logout_redirect_uri is the
    /logout-callback/ route — it must be registered at the IdP — and the
    session is kept alive until the callback validates the returned state).

    Mobile apps run this same flow in the system browser (which holds both the
    Django session cookie handed over at login and the IdP SSO cookie) and add
    `mobile_scheme` so the round-trip ends on a deep link closing the sheet —
    see `OIDCLogoutCallbackView`.
    """

    def post(self, request: WSGIRequest) -> HttpResponse:
        """Log the user out of the IdP session, then out of Django."""
        mobile_scheme = get_requested_mobile_scheme(request)

        logout_url = self.redirect_url

        if request.user.is_authenticated or request.session.get("oidc_id_token"):
            logout_url = self.construct_oidc_logout_url(request)

        if logout_url == self.redirect_url:
            # No IdP round-trip possible: end the local session right away.
            auth.logout(request)
            if mobile_scheme:
                return AppSchemeRedirect(f"{mobile_scheme}://logout")
        else:
            if mobile_scheme:
                request.session[MOBILE_LOGOUT_SESSION_KEY] = {
                    "scheme": mobile_scheme,
                    # The state generated by construct_oidc_logout_url() ties
                    # the flag to this round-trip only.
                    "state": parse_qs(urlparse(logout_url).query)["state"][0],
                    "created_at": time.time(),
                }
            # Persist the state generated in construct_oidc_logout_url()
            # (and the mobile flag) before the browser leaves for the IdP.
            request.session.modified = True
            request.session.save()

        return HttpResponseRedirect(logout_url)


class OIDCLogoutCallbackView(LaSuiteOIDCLogoutCallbackView):
    """Logout callback view handing control back to the mobile app.

    A logout initiated by a mobile app must end on a deep link so the
    system-browser sheet closes and the app can clear its local state; the
    parent view otherwise lands on LOGOUT_REDIRECT_URL, leaving the sheet
    open on the web homepage.
    """

    def get(self, request: WSGIRequest) -> HttpResponse:
        """Redirect to the app once the parent view has ended the session."""
        # The one-shot flag is only consumed on the final IdP callback of the
        # very logout that set it — a state known to this session *and* the one
        # stored alongside the flag: the parent redirects without ending the
        # session on the state-less "preflight" some providers send before the
        # actual callback, and raises on an unknown state, while an overlapping
        # web logout carries another valid state. Popping on any of those would
        # close the sheet while the browser session is still authenticated,
        # then leave the real callback without its flag.
        # Popped before super(): auth.logout() flushes the session.
        mobile_logout = None
        state = request.GET.get("state")
        if state and state in request.session.get("oidc_states", {}):
            mobile_logout = pop_mobile_flag(
                request.session, MOBILE_LOGOUT_SESSION_KEY, state=state
            )
        response = super().get(request)
        if mobile_logout is None:
            return response
        return AppSchemeRedirect(f"{mobile_logout['scheme']}://logout")
