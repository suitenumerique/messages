"""Sender trust: whether an inbound sender is a known correspondent of a mailbox.

The inbound pipeline asks a pluggable backend (``SENDER_TRUST_BACKEND``) and
uses a ``TRUSTED`` answer as a floor against the Junk folder.
"""
