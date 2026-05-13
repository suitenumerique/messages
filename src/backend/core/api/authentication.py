"""Authentication classes for service-to-service API calls.

This module provides:

- ``ServiceJWTAuthentication`` — shared base for JWT-based service auth.
  Validates ``Authorization: Bearer <JWT>`` tokens signed with HS256.
  Subclasses override ``get_secret()`` and ``handle_payload()`` to customise
  secret selection and post-validation logic. Used by MTA inbound.

- ``ChannelJwtAuthentication`` — authenticates a Channel via a session JWT
  in the ``X-Channel-Token`` header. The JWT must contain ``channel_id``
  and ``exp``. Scopes are read from the channel's database row (not the
  JWT) and enforced per HTTP method.

- ``ChannelApiKeyAuthentication`` — authenticates as an ``api_key`` Channel
  via the ``X-Channel-Id`` + ``X-API-Key`` headers.

The downstream permission layer (``HasChannelScope``) is scheme-agnostic —
it only inspects ``request.auth``.
"""

import hashlib
import secrets as secrets_mod

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

import jwt
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from core import models
from core.enums import ChannelTypes


# ---------------------------------------------------------------------------
# Shared JWT base class
# ---------------------------------------------------------------------------


class ServiceJWTAuthentication(BaseAuthentication):
    """Shared base for ``Authorization: Bearer <JWT>`` service auth.

    The JWT must be HS256-signed and contain an ``exp`` claim. If the
    request has a body, the JWT ``body_hash`` claim (SHA-256 hex digest)
    is verified against the actual body — this is how the MTA-in service
    proves request integrity and is reused by the client-bridge submit
    flow.

    Subclasses MUST implement:
    - ``get_secret(request)`` → the HMAC secret to verify the signature.
    - ``handle_payload(request, payload)`` → ``(user, auth)`` tuple or
      raise ``AuthenticationFailed``.

    Set ``require_body_hash = False`` on a subclass to skip the body-hash
    check (useful for non-body endpoints like MTA check-recipients where
    the body is JSON, not raw MIME).
    """

    require_body_hash = True
    jwt_require_claims = ["exp"]

    def get_secret(self, request):
        """Return the HMAC secret used to verify the JWT signature."""
        raise NotImplementedError

    def handle_payload(self, request, payload):
        """Process a validated JWT payload. Return ``(user, auth)``."""
        raise NotImplementedError

    # -- public DRF interface ------------------------------------------------

    def authenticate(self, request):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return None

        secret = self.get_secret(request)
        if not secret:
            return None

        jwt_token = auth_header.split(" ", 1)[1]

        try:
            payload = jwt.decode(
                jwt_token,
                secret,
                algorithms=["HS256"],
                options={
                    "require": self.jwt_require_claims,
                    "verify_exp": True,
                    "verify_signature": True,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationFailed("Token has expired.") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationFailed("Invalid token.") from exc

        # Body-hash integrity check
        if self.require_body_hash and request.body:
            expected_hash = payload.get("body_hash")
            if not expected_hash:
                raise AuthenticationFailed("Missing body_hash in token.")
            actual_hash = hashlib.sha256(request.body).hexdigest()
            if not secrets_mod.compare_digest(actual_hash, expected_hash):
                raise AuthenticationFailed("Body hash mismatch.")

        return self.handle_payload(request, payload)

    def authenticate_header(self, request):
        return 'Bearer realm="service"'


# ---------------------------------------------------------------------------
# Channel JWT authentication (X-Channel-Token)
# ---------------------------------------------------------------------------


class ChannelJwtAuthentication(BaseAuthentication):
    """Authenticate a Channel via a session JWT in ``X-Channel-Token``.

    The JWT must be HS256-signed with ``settings.CLIENTBRIDGE_API_SECRET``
    and contain ``channel_id`` and ``exp``. All other claims are ignored —
    scopes are always read from the channel's database row.

    On success ``request.user`` is set to ``channel.user`` (or
    ``AnonymousUser`` when the channel has no owner) and ``request.auth``
    is set to the ``Channel`` instance.

    This class is **not** in ``DEFAULT_AUTHENTICATION_CLASSES``.  Views
    that accept JWT-authenticated channels must add it explicitly to their
    ``authentication_classes`` and pair it with an appropriate scope
    permission (e.g. ``channel_scope(ChannelScope.MESSAGES_SEND)``).
    """

    def authenticate(self, request):
        token = request.headers.get("X-Channel-Token", "")
        if not token:
            return None

        secret = getattr(settings, "CLIENTBRIDGE_API_SECRET", "")
        if not secret:
            return None

        try:
            payload = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                options={"require": ["exp", "channel_id"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationFailed(
                "Session expired. Please re-authenticate."
            ) from exc
        except jwt.InvalidTokenError:
            return None  # Not our token — let other auth classes try

        channel_id = payload.get("channel_id")
        if not channel_id:
            return None

        try:
            channel = models.Channel.objects.select_related(
                "user",
                "mailbox__domain",
            ).get(id=channel_id)
        except (models.Channel.DoesNotExist, ValueError) as exc:
            raise AuthenticationFailed("Invalid channel.") from exc

        user = channel.user if channel.user_id else AnonymousUser()
        return (user, channel)

    def authenticate_header(self, request):
        return "X-Channel-Token"


# ---------------------------------------------------------------------------
# Channel API-key authentication (X-Channel-Id + X-API-Key)
# ---------------------------------------------------------------------------


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
            if isinstance(stored, str) and secrets_mod.compare_digest(stored, provided_hash):
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
