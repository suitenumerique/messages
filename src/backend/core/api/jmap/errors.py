"""JMAP error types as per RFC 8620."""


class JMAPError(Exception):
    """Base JMAP error."""

    error_type: str = "serverFail"

    def __init__(self, description: str = ""):
        self.description = description
        super().__init__(description)

    def to_response(self, call_id: str) -> list:
        """Convert to JMAP error response tuple."""
        return [
            "error",
            {"type": self.error_type, "description": self.description},
            call_id,
        ]


class UnknownCapabilityError(JMAPError):
    """The request used a capability not advertised in the session."""

    error_type = "unknownCapability"


class UnknownMethodError(JMAPError):
    """The method name is not recognized."""

    error_type = "unknownMethod"


class InvalidArgumentsError(JMAPError):
    """One or more arguments are of the wrong type or invalid."""

    error_type = "invalidArguments"


class InvalidResultReferenceError(JMAPError):
    """A result reference could not be resolved."""

    error_type = "invalidResultReference"


class AccountNotFoundError(JMAPError):
    """The accountId does not correspond to a valid account."""

    error_type = "accountNotFound"


class ForbiddenError(JMAPError):
    """The action is forbidden for the authenticated user."""

    error_type = "forbidden"


class ServerFailError(JMAPError):
    """An unexpected server error occurred."""

    error_type = "serverFail"
