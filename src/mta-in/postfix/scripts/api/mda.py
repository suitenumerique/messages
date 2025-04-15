from urllib3.util import Retry
from requests import Session
from requests.adapters import HTTPAdapter
import jwt
import datetime
import hashlib


with open("/etc/st-messages/env/MDA_API_BASE_URL", "r") as f:
    MDA_API_BASE_URL = f.read().strip()

with open("/etc/st-messages/env/MDA_API_SECRET", "r") as f:
    MDA_API_SECRET = f.read().strip()

mda_session = Session()
retries = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[500, 502, 503, 504],
    allowed_methods={'POST'},
)
mda_session.mount('https://', HTTPAdapter(max_retries=retries))

def mda_api_call(path, content_type, body, metadata):
    jwt_token = jwt.encode(
        {
            "exp": datetime.datetime.now() + datetime.timedelta(seconds=60),
            "body_hash": hashlib.sha256(body).hexdigest(),
            **metadata
        },
        MDA_API_SECRET,
        algorithm="HS256"
    )
    headers = {
        'Content-Type': content_type,
        'Authorization': f'Bearer {jwt_token}'
    }
    response = mda_session.post(MDA_API_BASE_URL+path, data=body, headers=headers, timeout=30)
    return response.json()