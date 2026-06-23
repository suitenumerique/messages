"""Production entrypoint: ``python -m realtime_relay``.

The distroless production image has no shell, so the listen port is read here
(from ``PORT``, default 8000) rather than expanded in a CMD. Dev/compose and the
Scalingo co-host invoke ``uvicorn`` directly instead.
"""

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "realtime_relay.app:app",
        host="0.0.0.0",  # noqa: S104 - bind all interfaces inside the container
        port=int(os.environ.get("PORT", "8000")),
        timeout_keep_alive=75,
        access_log=False,
    )


if __name__ == "__main__":
    main()
