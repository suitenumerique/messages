"""DRF authentication class for client-bridge requests.

``ClientBridgeChannelAuthentication`` validates a JWT issued by the auth
endpoint. The JWT encodes channel_id, mailbox_id, and role, and has an
expiration. Used in DEFAULT_AUTHENTICATION_CLASSES for regular API
requests, and on the submit endpoint. Enforces channel roles.

TODO: In the future, add geolocation filtering and/or IP allowlist
filtering here, based on channel.settings["allowed_ips"] or
channel.settings["allowed_countries"].
"""

from django.conf import settings
from django.urls import reverse

import jwt
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

from core import models
from core.enums import (
    CLIENT_BRIDGE_ROLES_CAN_EDIT,
    CLIENT_BRIDGE_ROLES_CAN_READ,
    CLIENT_BRIDGE_ROLES_CAN_SEND,
)

# HTTP methods considered safe (read-only)
_SAFE_METHODS = frozenset(("GET", "HEAD", "OPTIONS"))


class ClientBridgeChannelAuthentication(BaseAuthentication):
    """Authenticate API requests from the client-bridge via a session JWT.

    Expects: X-Channel-Token: <JWT issued by /client-bridge/auth/>

    The JWT contains channel_id, mailbox_id, mailbox_email, and role,
    and is signed with CLIENTBRIDGE_API_SECRET. When valid (and not
    expired), resolves the channel's user so existing queryset filtering
    works naturally. Also enforces the channel's role:

    - reader: GET/HEAD/OPTIONS only (read-only IMAP access)
    - editor: read + write (POST/PATCH/PUT/DELETE) — IMAP flag updates, etc.
    - sender: full access — read + edit + send (IMAP + SMTP)
    - sender_only: POST only (submit/send endpoints check CAN_SEND)
    """

    def authenticate(self, request):
        if not settings.FEATURE_CLIENTBRIDGE:
            return None

        token = request.headers.get("X-Channel-Token", "")
        if not token:
            return None

        secret = settings.CLIENTBRIDGE_API_SECRET
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
            ).get(
                id=channel_id,
                type="client-bridge",
                user__isnull=False,
            )
        except (models.Channel.DoesNotExist, ValueError) as exc:
            raise AuthenticationFailed("Invalid channel.") from exc

        # Enforce channel role from the database (not the JWT) so that
        # role changes take effect immediately without waiting for token expiry.
        role = (channel.settings or {}).get("role", "sender")
        method = request.method

        if method in _SAFE_METHODS:
            if role not in CLIENT_BRIDGE_ROLES_CAN_READ:
                raise PermissionDenied("This channel does not have read access.")
        elif method == "POST":
            if role in CLIENT_BRIDGE_ROLES_CAN_EDIT:
                pass  # editor, sender can POST anywhere
            elif role in CLIENT_BRIDGE_ROLES_CAN_SEND:
                # sender_only can only POST to the submit endpoint
                if request.path != reverse("client-bridge-submit"):
                    raise PermissionDenied("This channel can only submit messages.")
            else:
                raise PermissionDenied("This channel does not have write access.")
        elif role not in CLIENT_BRIDGE_ROLES_CAN_EDIT:
            # PATCH, PUT, DELETE always require edit access.
            raise PermissionDenied("This channel does not have edit access.")

        return (channel.user, channel)
