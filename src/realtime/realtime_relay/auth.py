"""Connection authentication.

The relay has no database and no session store. It trusts a short-lived JWT
the Django backend mints for an already-authenticated user. Authorization is
the union of the connection's own ``user:<sub>`` channel (always granted, from
the ``sub`` claim) and any extra rooms the token's ``rooms`` claim lists (e.g.
``thread:<id>`` the user has access to). The relay joins exactly those and
nothing else, so room ACLs live in Django where the data is.
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt


class AuthError(Exception):
    """Raised when a connection token is missing, invalid, or expired."""


@dataclass(frozen=True)
class Principal:
    user_id: str
    rooms: tuple[str, ...]


def authenticate(token: str | None, *, secret: str, algorithm: str) -> Principal:
    """Verify a connection token and return the authorized principal.

    Raises ``AuthError`` on any problem — the caller turns that into a 401.
    Signature, expiry (``exp``) and the presence of ``sub`` are all enforced.
    A valid token always joins at least its own ``user:<sub>`` channel; the
    ``rooms`` claim only adds extra channels.
    """
    if not token:
        raise AuthError("missing token")
    if not secret:
        # Fail closed: an unconfigured secret must never accept everything.
        raise AuthError("relay misconfigured: no jwt secret")

    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"invalid token: {exc}") from exc

    user_id = claims.get("sub")
    if not user_id:
        raise AuthError("token missing sub")

    rooms = claims.get("rooms")
    # The user's own channel is always implied, even if the backend forgot to
    # list it — a connection can always receive its owner's events.
    resolved: list[str] = [f"user:{user_id}"]
    if isinstance(rooms, list):
        for room in rooms:
            if isinstance(room, str) and room and room not in resolved:
                resolved.append(room)

    return Principal(user_id=str(user_id), rooms=tuple(resolved))
