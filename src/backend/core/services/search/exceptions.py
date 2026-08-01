"""Exceptions raised by the search service."""

from opensearchpy.exceptions import ConnectionError as OpenSearchConnectionError
from opensearchpy.exceptions import TransportError


class TransientTransportError(TransportError):
    """Wraps a ``TransportError`` whose status is in ``RETRYABLE_TRANSPORT_STATUS``.

    Lets the ``retry_on`` lists stay a tuple of exception types
    instead of relying on a status-code filter buried in a handler.
    """


# Transport status codes that warrant a task-level retry: the cluster is
# alive enough to respond but the request itself could not be served right
# now (rolling restart, throttling, gateway hiccup). 4xx responses other
# than 429 are caller bugs (bad mapping, malformed query) — retrying them
# only burns worker time. 500/501 are cluster bugs — same.
RETRYABLE_TRANSPORT_STATUS = frozenset({429, 502, 503, 504})

# The retryable exception set, shared between every task's ``retry_on``
# declaration and the index helpers. Two invariants must hold together:
# - any exception the helpers let propagate is in this tuple;
# - any exception in this tuple is allowed to propagate (not swallowed by a
#   broad ``except Exception``) so the surrounding task actually retries.
# Socket-level drops surface as ``OpenSearchConnectionError``; retryable HTTP
# statuses surface as ``TransientTransportError``.
RETRYABLE_EXCEPTIONS = (OpenSearchConnectionError, TransientTransportError)
