"""Fixtures for JMAP API tests."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

import pytest
from jmapc import Ref
from rest_framework.test import APIClient

from core import enums, factories


@pytest.fixture
def api_client():
    """Provide an instance of the API client for tests."""
    return APIClient()


@pytest.fixture
def user():
    """Create a test user."""
    return factories.UserFactory()


@pytest.fixture
def mailbox(user):
    """Create a mailbox with user access."""
    return factories.MailboxFactory(users_read=[user])


@pytest.fixture
def mailbox_with_threads(user):
    """Create a mailbox with multiple threads and messages."""
    mailbox = factories.MailboxFactory(users_read=[user])

    # Create 5 threads with messages
    for i in range(5):
        thread = factories.ThreadFactory(subject=f"Thread {i}")
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        # Create a contact for the sender in this mailbox
        sender = factories.ContactFactory(mailbox=mailbox)

        # Create 1-3 messages per thread
        for j in range(1, (i % 3) + 2):
            factories.MessageFactory(
                thread=thread,
                subject=f"Message {j} in Thread {i}",
                sender=sender,
                created_at=timezone.now() - timedelta(days=i, hours=j),
            )

        thread.update_stats()

    return mailbox


class JMAPTestClient:
    """
    A test client that adapts DRF's APIClient to work with jmapc method objects.

    This allows tests to use jmapc's MailboxQuery, EmailGet, etc. classes
    while still using Django's test client for actual HTTP requests.
    """

    def __init__(self, api_client: APIClient, user, account_id: str):
        self.api_client = api_client
        self.user = user
        self.account_id = account_id
        self.api_url = f"/api/{settings.API_VERSION}/jmap/"
        self.session_url = f"/api/{settings.API_VERSION}/jmap/session"

        # Authenticate the client
        self.api_client.force_authenticate(user=user)

    def get_session(self):
        """Get the JMAP session."""
        response = self.api_client.get(self.session_url)
        assert response.status_code == 200, f"Session request failed: {response.data}"
        return response.data

    def request(self, methods, raise_errors: bool = True):
        """
        Execute JMAP method calls.

        Args:
            methods: A single jmapc Method object or a list of Method objects.
            raise_errors: If True, raise an exception on JMAP errors.

        Returns:
            List of response tuples or single response if one method was passed.
        """
        # Normalize to list
        if not isinstance(methods, (list, tuple)):
            methods = [methods]
            single = True
        else:
            single = False

        # Build the JMAP request
        method_calls = []
        using = {"urn:ietf:params:jmap:core"}

        # First pass: generate all call_ids for Ref resolution
        self._call_ids = []
        for i, method in enumerate(methods):
            method_name = self._get_method_name(method)
            call_id = f"{i}.{method_name}"
            self._call_ids.append(call_id)

        # Second pass: serialize methods with Ref resolution
        for i, method in enumerate(methods):
            method_name = self._get_method_name(method)
            args = self._serialize_method(method, call_index=i)
            call_id = self._call_ids[i]
            method_calls.append([method_name, args, call_id])

            # Add required capabilities
            if hasattr(method, "using"):
                using.update(method.using)
            if (
                "Mail" in method_name
                or "Email" in method_name
                or "Thread" in method_name
            ):
                using.add("urn:ietf:params:jmap:mail")
            if "Submission" in method_name or "Identity" in method_name:
                using.add("urn:ietf:params:jmap:submission")

        request_data = {
            "using": list(using),
            "methodCalls": method_calls,
        }

        # Make the request
        response = self.api_client.post(self.api_url, request_data, format="json")
        assert response.status_code == 200, f"JMAP request failed: {response.data}"

        # Parse responses
        method_responses = response.data.get("methodResponses", [])
        results = []

        for resp in method_responses:
            method_name, result, call_id = resp

            if method_name == "error" and raise_errors:
                raise JMAPTestError(
                    f"JMAP error: {result.get('type')}: {result.get('description')}"
                )

            results.append(JMAPTestResponse(method_name, result, call_id))

        if single and len(results) == 1:
            return results[0]
        return results

    def _get_method_name(self, method) -> str:
        """Get the JMAP method name from a jmapc method object."""
        if hasattr(method, "jmap_method_name"):
            return method.jmap_method_name
        # Fallback: construct from class name
        class_name = method.__class__.__name__
        # Convert CamelCase to JMAP format (e.g., MailboxQuery -> Mailbox/query)
        for noun in ["Mailbox", "EmailSubmission", "Email", "Thread", "Identity"]:
            if class_name.startswith(noun):
                verb = class_name[len(noun) :].lower()
                return f"{noun}/{verb}"
        return class_name

    def _serialize_method(self, method, call_index: int = 0) -> dict:
        """Serialize a jmapc method object to a dict of arguments."""
        # Always use manual serialization to handle Ref objects properly
        # (jmapc's to_dict() tries to resolve Refs which requires context)
        data = {}

        # Get dataclass fields if available
        if hasattr(method, "__dataclass_fields__"):
            for field_name, _field_info in method.__dataclass_fields__.items():
                if field_name.startswith("_"):
                    continue
                value = getattr(method, field_name, None)
                if value is not None:
                    # Convert snake_case to camelCase
                    camel_name = self._to_camel_case(field_name)
                    data[camel_name] = value
        else:
            # Fallback: serialize all non-private attributes
            for field_name in dir(method):
                if field_name.startswith("_"):
                    continue
                if field_name in ("using", "jmap_method_name", "to_dict", "to_json"):
                    continue
                value = getattr(method, field_name)
                if callable(value):
                    continue
                if value is not None:
                    camel_name = self._to_camel_case(field_name)
                    data[camel_name] = value

        # Process Ref objects in the data and convert to JMAP back-reference format
        data = self._process_refs(data, call_index)

        # Always add accountId
        if "accountId" not in data:
            data["accountId"] = self.account_id

        return data

    def _process_refs(self, data: dict, call_index: int) -> dict:
        """Process Ref objects and convert them to JMAP back-reference format."""
        result = {}
        for key, value in data.items():
            if isinstance(value, Ref):
                # Convert Ref to JMAP ResultReference format
                # Ref.method can be -1 (previous), a string (method name), or int (index)
                ref_method = getattr(value, "method", -1)
                if ref_method == -1:
                    # Reference the previous method
                    ref_index = call_index - 1
                    ref_call_id = self._call_ids[ref_index] if ref_index >= 0 else "0"
                    ref_method_name = (
                        ref_call_id.split(".", 1)[1]
                        if "." in ref_call_id
                        else ref_call_id
                    )
                elif isinstance(ref_method, int):
                    ref_call_id = self._call_ids[ref_method]
                    ref_method_name = (
                        ref_call_id.split(".", 1)[1]
                        if "." in ref_call_id
                        else ref_call_id
                    )
                else:
                    ref_method_name = ref_method
                    # Find the call_id for this method
                    ref_call_id = None
                    for cid in self._call_ids:
                        if ref_method_name in cid:
                            ref_call_id = cid
                            break
                    if not ref_call_id:
                        ref_call_id = f"0.{ref_method_name}"

                # Use #key format for JMAP back-reference
                result[f"#{key}"] = {
                    "resultOf": ref_call_id,
                    "name": ref_method_name,
                    "path": value.path,
                }
            elif isinstance(value, dict):
                # Check if this is already a serialized Ref (from to_dict())
                if "__ref" in value and value.get("__ref") == "Ref":
                    # This is a serialized Ref object
                    ref_method = value.get("method", -1)
                    path = value.get("path", "/")
                    if ref_method == -1:
                        ref_index = call_index - 1
                        ref_call_id = (
                            self._call_ids[ref_index] if ref_index >= 0 else "0"
                        )
                        ref_method_name = (
                            ref_call_id.split(".", 1)[1]
                            if "." in ref_call_id
                            else ref_call_id
                        )
                    else:
                        ref_method_name = str(ref_method)
                        ref_call_id = f"0.{ref_method_name}"
                    result[f"#{key}"] = {
                        "resultOf": ref_call_id,
                        "name": ref_method_name,
                        "path": path,
                    }
                else:
                    result[key] = self._process_refs(value, call_index)
            else:
                result[key] = value
        return result

    def _to_camel_case(self, snake_str: str) -> str:
        """Convert snake_case to camelCase."""
        components = snake_str.split("_")
        return components[0] + "".join(x.title() for x in components[1:])


class JMAPTestResponse:
    """A response from a JMAP method call."""

    def __init__(self, method_name: str, data: dict, call_id: str):
        self.method_name = method_name
        self.data = data
        self.call_id = call_id

    @property
    def ids(self):
        """Get the 'ids' field from the response (for query results)."""
        return self.data.get("ids", [])

    @property
    def list(self):
        """Get the 'list' field from the response (for get results)."""
        return self.data.get("list", [])

    @property
    def not_found(self):
        """Get the 'notFound' field from the response."""
        return self.data.get("notFound", [])

    def __repr__(self):
        return f"JMAPTestResponse({self.method_name}, {self.call_id})"


class JMAPTestError(Exception):
    """Exception raised when a JMAP method call fails."""

    pass


@pytest.fixture
def jmap_client(api_client, user):
    """Create a JMAP test client for the user."""
    return JMAPTestClient(api_client, user, str(user.id))
