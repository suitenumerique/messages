"""
Test suite for generated openapi schema.
"""

import json
from io import StringIO

from django.core.management import call_command
from django.test import Client

import pytest

pytestmark = pytest.mark.django_db


def test_openapi_client_schema():
    """
    Generated and served OpenAPI client schema should be correct.
    """
    # Start by generating the swagger.json file
    output = StringIO()
    call_command(
        "spectacular",
        "--api-version",
        "v1.0",
        "--urlconf",
        "core.urls",
        "--format",
        "openapi-json",
        "--file",
        "core/tests/swagger/swagger.json",
        stdout=output,
    )
    assert output.getvalue() == ""

    response = Client().get("/api/v1.0/swagger.json")

    assert response.status_code == 200
    with open(
        "core/tests/swagger/swagger.json", "r", encoding="utf-8"
    ) as expected_schema:
        assert response.json() == json.load(expected_schema)


@pytest.mark.parametrize(
    "schema_name,field",
    [
        # Mailbox.contact is a SET_NULL FK (an alias mailbox has none).
        ("MailboxAdmin", "contact"),
        # get_expected_dns_records returns None outside the retrieve action.
        ("MailDomainAdmin", "expected_dns_records"),
        # get_role / get_user_role return None when the user has no role in
        # the relevant scope (e.g. no mailbox_id in context on the split action).
        ("Mailbox", "role"),
        ("Thread", "user_role"),
    ],
)
def test_openapi_method_fields_are_nullable(schema_name, field):
    """Fields whose value can legitimately be ``null`` at runtime must be
    advertised as nullable in the schema, so the generated typed client does
    not assume a non-null value (regression: SerializerMethodField and nested
    serializers on nullable FKs don't inherit nullability automatically)."""
    response = Client().get("/api/v1.0/swagger.json")
    assert response.status_code == 200

    prop = response.json()["components"]["schemas"][schema_name]["properties"][field]

    assert prop.get("nullable") is True
