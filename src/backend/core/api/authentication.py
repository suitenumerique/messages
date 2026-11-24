"""Authentication classes for service-to-service API calls.

ChannelApiKeyAuthentication authenticates a request as an api_key Channel via
the X-Channel-Id + X-API-Key headers. AppPasswordAuthentication authenticates
a client-bridge Channel via the X-Mailbox-Email + X-App-Password headers.
New schemes (mTLS, signed JWT, OIDC client credentials, …) should be
added here as additional BaseAuthentication subclasses that set
``request.auth`` to a Channel instance the same way. The downstream permission
layer (``HasChannelScope``) is scheme-agnostic — it only inspects
``request.auth``.
"""

import hashlib
from secrets import compare_digest

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

from core import models
from core.enums import ChannelScopeLevel, ChannelTypes
from core.mda.addresses import ascii_lower, normalize_domain, split_address


class ChannelApiKeyAuthentication(BaseAuthentication):
    """Authenticate as an api_key Channel via X-Channel-Id + X-API-Key.

    Client contract:
        X-Channel-Id: <uuid>     (public, identifies which channel)
        X-API-Key: <raw secret>  (the shared secret, hashed at rest)

    On success ``request.user`` is set to ``AnonymousUser`` (there is no
    associated user) and ``request.auth`` is set to the authenticated
    ``Channel`` instance. Views must read ``request.auth.scope_level``,
    ``request.auth.mailbox_id`` and ``request.auth.maildomain_id`` to
    enforce resource-level bounds on the action they perform.
    """

    def authenticate(self, request):
        channel_id = request.headers.get("X-Channel-Id")
        api_key = request.headers.get("X-API-Key")

        # Missing either header → this auth scheme does not apply; let DRF
        # try the next class in authentication_classes. Returning None here
        # is the documented way to skip.
        if not channel_id or not api_key:
            return None

        try:
            # is_active=True is part of the lookup on purpose: a paused
            # channel must be indistinguishable from a non-existent one, so
            # it fails with the same generic error rather than leaking that
            # the channel exists but is disabled. The same goes for a
            # personal channel whose owner has been deactivated: suspending
            # a user must also suspend their api_keys. Other scope levels
            # only reference their creator for audit and stay usable.
            channel = (
                models.Channel.objects.select_related("mailbox", "maildomain", "user")
                .filter(
                    ~Q(scope_level=ChannelScopeLevel.USER) | Q(user__is_active=True)
                )
                .get(pk=channel_id, type=ChannelTypes.API_KEY, is_active=True)
            )
        except (models.Channel.DoesNotExist, ValueError, DjangoValidationError) as exc:
            # ValueError / ValidationError handle malformed UUIDs.
            raise AuthenticationFailed("Invalid channel or API key.") from exc

        provided_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        stored_hashes = (channel.encrypted_settings or {}).get("api_key_hashes") or []
        # Iterate every stored hash without early exit so the timing is
        # constant with respect to *which* slot matched (the total number
        # of slots is not secret — there is no hard cap on the array). Any
        # match flips the boolean.
        matched = False
        for stored in stored_hashes:
            if isinstance(stored, str) and compare_digest(stored, provided_hash):
                matched = True
        if not matched:
            raise AuthenticationFailed("Invalid channel or API key.")

        expires_at_raw = (channel.settings or {}).get("expires_at")
        if expires_at_raw:
            expires_at = parse_datetime(expires_at_raw)
            if expires_at is not None and expires_at < timezone.now():
                raise AuthenticationFailed("API key has expired.")

        # Throttled update of last_used_at for monitoring (5 min window).
        channel.mark_used()

        return (AnonymousUser(), channel)

    def authenticate_header(self, request):
        # DRF uses this as the WWW-Authenticate header on 401 responses.
        return "X-API-Key"


_UNDECLARED = object()


def app_password_scope(request):
    """Scope the matched view requires from an app password, or ``_UNDECLARED``.

    Views list them in ``app_password_scopes``, keyed by viewset action or,
    for plain APIViews, by lowercase HTTP method. ``None`` as the scope means
    any app password.
    """
    view = getattr(request.resolver_match, "func", None)
    declared = getattr(getattr(view, "cls", None), "app_password_scopes", None) or {}
    actions = getattr(view, "actions", None)
    method = request.method.lower()
    key = actions.get(method) if actions else method
    return declared.get(key, _UNDECLARED)


class AppPasswordAuthentication(BaseAuthentication):
    """Authenticate as a client-bridge Channel via mailbox address + app password.

    Client contract:
        X-Mailbox-Email: <mailbox address>
        X-App-Password:  <app password>

    The client-bridge service sends these on every request, relaying what the
    email client gave at IMAP/SMTP login. ``request.user`` is the user who
    created the channel, so the regular endpoints and their querysets apply
    unchanged, and ``request.auth`` is the Channel.

    Default deny: only views declaring an ``app_password_scopes`` entry for
    the request can be reached, and only if the channel holds that scope.
    Scopes are read from the database on every request, so a change or a
    password rotation applies at once.
    """

    def authenticate(self, request):
        email = request.headers.get("X-Mailbox-Email")
        password = request.headers.get("X-App-Password")
        if not email or not password or not settings.FEATURE_CLIENTBRIDGE:
            return None

        parts = split_address(email)
        if parts is None:
            raise AuthenticationFailed("Invalid mailbox or app password.")
        digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
        channels = models.Channel.objects.select_related(
            "user", "mailbox__domain"
        ).filter(
            type=ChannelTypes.CLIENT_BRIDGE,
            is_active=True,
            user__is_active=True,
            mailbox__local_part=ascii_lower(parts[0]),
            mailbox__domain__name=normalize_domain(parts[1]),
        )
        # No early exit, so timing doesn't tell which channel matched.
        matched = None
        for channel in channels:
            stored = (channel.encrypted_settings or {}).get("password_hash")
            if isinstance(stored, str) and compare_digest(stored, digest):
                matched = channel
        if matched is None:
            raise AuthenticationFailed("Invalid mailbox or app password.")

        required = app_password_scope(request)
        scopes = (matched.settings or {}).get("scopes")
        if required is _UNDECLARED or (
            required is not None
            and not (isinstance(scopes, list) and required in scopes)
        ):
            raise PermissionDenied("This app password does not allow this request.")

        matched.mark_used()
        return (matched.user, matched)

    def authenticate_header(self, request):
        return "X-App-Password"
