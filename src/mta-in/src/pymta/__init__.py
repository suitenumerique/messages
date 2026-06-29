"""
Pure-Python inbound MTA built on aiosmtpd.

Reception-side counterpart to the Postfix+milter implementation that lives in
``src/mta-in/src/delivery_milter.py``. Both share the same MDA REST contract
(``inbound/mta/check/`` + ``inbound/mta/deliver/``), and both run as a
stateless, queue-less SMTP front-end: each SMTP session blocks on the
synchronous delivery HTTP call and translates the outcome straight back to the
remote peer.
"""
