"""realtime-relay: SSE fan-out service backed by a Redis bus.

See ``app.py`` for the ASGI entrypoint and ``README.md`` for the contract
shared with the Django backend (token claims + Redis channel naming).
"""

__version__ = "0.1.0"
