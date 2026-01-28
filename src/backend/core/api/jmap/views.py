"""JMAP API views."""

from datetime import datetime
from datetime import timezone as dt_timezone

from django.conf import settings

from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from core import models
from core.api.permissions import IsAuthenticated

from .errors import (
    JMAPError,
)
from .methods import JMAPContext, MethodRegistry, resolve_args

# JMAP capabilities we support
JMAP_CAPABILITIES = {
    "urn:ietf:params:jmap:core": {
        "maxSizeUpload": 50000000,
        "maxConcurrentUpload": 4,
        "maxSizeRequest": 10000000,
        "maxConcurrentRequests": 4,
        "maxCallsInRequest": 16,
        "maxObjectsInGet": 500,
        "maxObjectsInSet": 500,
        "collationAlgorithms": ["i;ascii-casemap", "i;octet"],
    },
    "urn:ietf:params:jmap:mail": {
        "maxMailboxesPerEmail": None,
        "maxMailboxDepth": None,
        "maxSizeMailboxName": 255,
        "maxSizeAttachmentsPerEmail": 50000000,
        "emailQuerySortOptions": ["receivedAt", "sentAt", "size", "subject"],
        "mayCreateTopLevelMailbox": True,
    },
    "urn:ietf:params:jmap:submission": {
        "maxDelayedSend": 0,
        "submissionExtensions": {},
    },
}


class JMAPSessionView(APIView):
    """
    JMAP Session Resource.

    GET request returns the session object with capabilities, accounts, and URLs.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Return the JMAP session object."""
        user = request.user

        # Get user's accessible mailboxes for account info
        mailboxes = models.Mailbox.objects.filter(accesses__user=user).select_related(
            "domain"
        )

        # Use the first mailbox email as the account name, or user email
        primary_email = None
        for mailbox in mailboxes[:1]:
            primary_email = f"{mailbox.local_part}@{mailbox.domain.name}"
            break

        if not primary_email:
            primary_email = user.email or str(user.id)

        # Build account
        account_id = str(user.id)
        accounts = {
            account_id: {
                "name": primary_email,
                "isPersonal": True,
                "isReadOnly": False,
                "accountCapabilities": {
                    "urn:ietf:params:jmap:mail": {},
                },
            }
        }

        # Build the base URL for API endpoints
        base_url = request.build_absolute_uri(f"/api/{settings.API_VERSION}/jmap/")

        session = {
            "capabilities": JMAP_CAPABILITIES,
            "accounts": accounts,
            "primaryAccounts": {
                "urn:ietf:params:jmap:mail": account_id,
            },
            "username": primary_email,
            "apiUrl": base_url,
            "downloadUrl": f"{base_url}download/{{accountId}}/{{blobId}}/{{name}}",
            "uploadUrl": f"{base_url}upload/{{accountId}}/",
            "eventSourceUrl": f"{base_url}eventsource/?types={{types}}&closeafter={{closeafter}}&ping={{ping}}",
            "state": datetime.now(dt_timezone.utc).isoformat(),
        }

        return Response(session)


class JMAPAPIView(APIView):
    """
    JMAP API endpoint for method calls.

    POST request accepts JSON body with method calls and returns responses.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request):
        """Process JMAP method calls."""
        data = request.data

        # Validate request structure
        if not isinstance(data, dict):
            return Response(
                {"type": "urn:ietf:params:jmap:error:notRequest"},
                status=400,
            )

        using = data.get("using", [])
        method_calls = data.get("methodCalls", [])

        if not isinstance(using, list) or not isinstance(method_calls, list):
            return Response(
                {"type": "urn:ietf:params:jmap:error:notRequest"},
                status=400,
            )

        # Validate capabilities
        for capability in using:
            if capability not in JMAP_CAPABILITIES:
                return Response(
                    {
                        "type": "urn:ietf:params:jmap:error:unknownCapability",
                        "unknownCapabilities": [capability],
                    },
                    status=400,
                )

        # Process method calls
        method_responses = []
        results_by_call_id = {}
        context = JMAPContext(request.user, results_by_call_id)

        for call in method_calls:
            if not isinstance(call, list) or len(call) != 3:
                method_responses.append(
                    [
                        "error",
                        {
                            "type": "invalidArguments",
                            "description": "Method call must be [name, args, callId]",
                        },
                        call[2] if len(call) > 2 else "unknown",
                    ]
                )
                continue

            method_name, args, call_id = call

            try:
                # Resolve back-references in arguments
                resolved_args = resolve_args(args, context)

                # Set current call_id for implicit responses
                context.current_call_id = call_id
                context.implicit_responses = []

                # Get and execute the method handler
                handler_class = MethodRegistry.get_handler(method_name)
                handler = handler_class(context)
                result = handler.execute(resolved_args)

                # Store result for back-references
                results_by_call_id[call_id] = result

                method_responses.append([method_name, result, call_id])

                # Append any implicit responses (e.g. from onSuccessUpdateEmail)
                for implicit in context.implicit_responses:
                    method_responses.append(implicit)

            except JMAPError as e:
                method_responses.append(e.to_response(call_id))
            except Exception as e:
                # Catch any unexpected errors
                method_responses.append(
                    [
                        "error",
                        {"type": "serverFail", "description": str(e)},
                        call_id,
                    ]
                )

        response = {
            "methodResponses": method_responses,
            "sessionState": datetime.now(dt_timezone.utc).isoformat(),
        }

        return Response(response)
