"""Authentication classes for service-to-service API calls.

Today this module ships a single scheme, ChannelApiKeyAuthentication, which
authenticates a request as an api_key Channel via the X-Channel-Id + X-API-Key
headers. New schemes (mTLS, signed JWT, OIDC client credentials, …) should be
added here as additional BaseAuthentication subclasses that set
``request.auth`` to a Channel instance the same way. The downstream permission
layer (``HasChannelScope``) is scheme-agnostic — it only inspects
``request.auth``.
"""

import hashlib
from secrets import compare_digest

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from core import models
from core.enums import ChannelTypes


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
            channel = models.Channel.objects.select_related(
                "mailbox", "maildomain", "user"
            ).get(pk=channel_id, type=ChannelTypes.API_KEY)
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
