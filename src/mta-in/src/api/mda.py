import datetime
import hashlib
import os

import jwt
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

MDA_API_BASE_URL = os.getenv("MDA_API_BASE_URL")
MDA_API_SECRET = os.getenv("MDA_API_SECRET")
MDA_API_TIMEOUT = int(os.getenv("MDA_API_TIMEOUT", "30"))

# The token is minted once but must stay valid across every retry attempt
# below (Retry total=5, backoff_factor=1) — each attempt can take up to
# MDA_API_TIMEOUT plus backoff sleeps between them. A fixed 60s expiry would
# lapse mid-retry and turn a transient 5xx into an auth failure. Size the TTL
# to comfortably cover the whole (timeout + backoff) * attempts window; the
# body_hash binding keeps a leaked token usable only for its exact request.
MDA_API_JWT_TTL = int(os.getenv("MDA_API_JWT_TTL", str(MDA_API_TIMEOUT * 10 + 60)))


def mda_api_call(path, content_type, body, metadata):
    mda_session = Session()
    retries = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods={"POST"},
    )
    mda_session.mount("https://", HTTPAdapter(max_retries=retries))

    now = datetime.datetime.now(datetime.timezone.utc)
    jwt_token = jwt.encode(
        {
            "exp": now + datetime.timedelta(seconds=MDA_API_JWT_TTL),
            # The channel is authenticated by the HMAC signature over the shared
            # MDA_API_SECRET; body_hash binds the token to its payload (sha256 of
            # an empty body for bodyless calls like /check) so a captured token
            # can't be repurposed for a different body within its short lifetime.
            # No jti/nonce: retries (and urllib3's) resend the same token, and
            # the backend trusts the secret rather than tracking single use.
            "body_hash": hashlib.sha256(body).hexdigest(),
            **metadata,
        },
        MDA_API_SECRET,
        algorithm="HS256",
    )
    headers = {"Content-Type": content_type, "Authorization": f"Bearer {jwt_token}"}
    response = mda_session.post(
        MDA_API_BASE_URL + path, data=body, headers=headers, timeout=MDA_API_TIMEOUT
    )
    return (response.status_code, response.json())
