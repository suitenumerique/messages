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


def mda_api_call(path, content_type, body, metadata):
    mda_session = Session()
    retries = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods={"POST"},
    )
    mda_session.mount("https://", HTTPAdapter(max_retries=retries))

    jwt_token = jwt.encode(
        {
            "exp": datetime.datetime.now() + datetime.timedelta(seconds=60),
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
