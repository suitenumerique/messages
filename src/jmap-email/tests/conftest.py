"""Shared test fixtures.

The parser has two "this should never happen" handlers that turn an
internal exception into a degraded result: ``parse_email`` returns
``None``, and the body-structure walk returns a message with no body
parts. Every fuzz oracle accepts both of those outcomes (``assert result
is None or isinstance(result, dict)``, ``if parsed is None: return``), so
on their own the fuzz tests cannot see the one thing they exist to find.

``fail_on_swallowed_exception`` closes that gap for fuzz-marked tests: if
either handler fires, the test fails with the swallowed exception instead
of passing on a degraded result.
"""

import logging

import pytest

# Log messages emitted by the parser's two catch-all handlers.
_SWALLOW_MARKERS = (
    "parse_email: unexpected error",
    "Error parsing message body structure",
)


class _SwallowDetector(logging.Handler):
    """Records the parser's catch-all handler firing."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.hits: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if any(marker in message for marker in _SWALLOW_MARKERS):
            exc = record.exc_info[1] if record.exc_info else None
            self.hits.append(f"{message} [{exc!r}]")


@pytest.fixture(autouse=True)
def fail_on_swallowed_exception(request):
    """Fail a fuzz test whose input made the parser swallow an exception."""
    if "fuzz" not in request.keywords:
        yield
        return

    logger = logging.getLogger("jmap_email")
    detector = _SwallowDetector()
    was_disabled = logger.disabled
    logger.disabled = False
    logger.addHandler(detector)
    try:
        yield
    finally:
        logger.removeHandler(detector)
        logger.disabled = was_disabled

    if detector.hits:
        raise AssertionError(
            "parser swallowed an exception on fuzz input "
            f"({len(detector.hits)} time(s)); first: {detector.hits[0]}"
        )
