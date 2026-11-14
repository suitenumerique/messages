"""
Pure-Python inbound MTA, on asyncio and the standard library.

Counterpart of the Postfix milter in ``src/mta-in/src/delivery_milter.py``.
Both use the same MDA REST contract (``inbound/mta/check/`` and
``inbound/mta/deliver/``) and keep no queue: an SMTP session waits for the
delivery HTTP call and passes its outcome on to the peer.
"""
