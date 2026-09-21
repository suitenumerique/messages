"""Tests for the Docs read-only client."""

import pytest
import requests
import responses

from core.services.docs import DocsClient, DocsError

CONFIG = {
    "base_url": "http://localhost:8071",
    "network_host": None,
    "api_prefix": "/api/v1.0",
    "session_cookie_name": "docs_sessionid",
    "page_size": 2,
    "max_documents": 10,
    "timeout_seconds": 5,
}
API = "http://localhost:8071/api/v1.0"
KEYCLOAK_AUTH = "http://localhost:8083/realms/impress/protocol/openid-connect/auth"
LOGIN_ACTION = "http://localhost:8083/realms/impress/login-actions/authenticate"
LOGIN_FORM = f'<html><form id="kc-form-login" action="{LOGIN_ACTION}?session_code=abc&amp;tab_id=t1" method="post"></form></html>'


def _register_login_page():
    responses.get(
        f"{API}/authenticate/",
        status=302,
        headers={
            "Location": KEYCLOAK_AUTH,
            "Set-Cookie": "docs_sessionid=anonymous; HttpOnly",
        },
    )
    responses.get(
        KEYCLOAK_AUTH,
        body=LOGIN_FORM,
        headers={
            "Set-Cookie": "AUTH_SESSION_ID=kc-session; Version=1; Path=/realms/impress/; Secure"
        },
    )


@responses.activate
def test_login_submits_keycloak_form_and_keeps_docs_session_cookie():
    """The Keycloak cookies go back to Keycloak and the Docs session is kept."""
    _register_login_page()
    login_post = responses.post(
        f"{LOGIN_ACTION}?session_code=abc&tab_id=t1",
        status=302,
        headers={"Location": f"{API}/callback/?code=123"},
    )
    responses.get(
        f"{API}/callback/?code=123",
        status=302,
        # Not registered: following it would raise a connection error.
        headers={
            "Location": "http://localhost:3000",
            "Set-Cookie": "docs_sessionid=authenticated; HttpOnly",
        },
    )
    me = responses.get(f"{API}/users/me/", json={"email": "user1@example.local"})

    client = DocsClient(CONFIG)
    client.login("user1@example.local", "secret")
    client.ensure_authenticated()

    login_request = login_post.calls[0].request
    assert login_request.headers["Cookie"] == "AUTH_SESSION_ID=kc-session"
    assert login_request.body == "username=user1%40example.local&password=secret"
    assert me.calls[0].request.headers["Cookie"] == "docs_sessionid=authenticated"


@responses.activate
def test_login_raises_when_keycloak_renders_the_form_again():
    """Wrong credentials: Keycloak answers 200 with its login form."""
    _register_login_page()
    responses.post(f"{LOGIN_ACTION}?session_code=abc&tab_id=t1", body=LOGIN_FORM)

    with pytest.raises(DocsError, match="invalid email or password"):
        DocsClient(CONFIG).login("user1@example.local", "wrong")


@responses.activate
def test_login_raises_when_login_form_is_missing():
    """A page without form (e.g. already logged in elsewhere) is reported."""
    responses.get(f"{API}/authenticate/", body="<html></html>")

    with pytest.raises(DocsError, match="login form not found"):
        DocsClient(CONFIG).login("user1@example.local", "secret")


@responses.activate
def test_network_host_replaces_localhost_and_keeps_host_header():
    """From a container, localhost is reached through the network host."""
    me = responses.get("http://172.17.0.1:8071/api/v1.0/users/me/", json={})

    DocsClient({**CONFIG, "network_host": "172.17.0.1"}).ensure_authenticated()

    assert me.calls[0].request.headers["Host"] == "localhost:8071"


@responses.activate
def test_session_id_is_sent_as_docs_session_cookie():
    """A session cookie copied from a browser avoids the login."""
    me = responses.get(f"{API}/users/me/", json={})

    DocsClient(CONFIG, session_id="browser-session").ensure_authenticated()

    assert me.calls[0].request.headers["Cookie"] == "docs_sessionid=browser-session"


@responses.activate
def test_ensure_authenticated_raises_on_401():
    """An expired session is reported as not authenticated."""
    responses.get(f"{API}/users/me/", status=401, json={})

    with pytest.raises(DocsError, match="not authenticated"):
        DocsClient(CONFIG, session_id="expired").ensure_authenticated()


@responses.activate
def test_iter_document_ids_follows_pagination_and_children():
    """Every page is read, and sub-pages are listed through ``children``."""
    responses.get(
        f"{API}/documents/?page_size=2",
        json={
            "next": f"{API}/documents/?page=2&page_size=2",
            "results": [{"id": "a", "numchild": 0}, {"id": "b", "numchild": 0}],
        },
    )
    responses.get(
        f"{API}/documents/?page=2&page_size=2",
        json={"next": None, "results": [{"id": "c", "numchild": 1}]},
    )
    responses.get(
        f"{API}/documents/c/children/?page_size=2",
        json={
            "next": None,
            "results": [{"id": "c1", "numchild": 0}, {"id": "a", "numchild": 0}],
        },
    )

    assert list(DocsClient(CONFIG).iter_document_ids()) == ["a", "b", "c", "c1"]


@responses.activate
def test_iter_document_ids_stops_at_max_documents():
    """The number of documents read is bounded."""
    responses.get(
        f"{API}/documents/?page_size=2",
        json={"next": None, "results": [{"id": "a"}, {"id": "b"}]},
    )

    assert list(DocsClient({**CONFIG, "max_documents": 1}).iter_document_ids()) == ["a"]


@responses.activate
def test_get_document_returns_markdown_and_names_untitled_documents():
    """An untitled document gets a unique name built from its id."""
    responses.get(
        f"{API}/documents/d1/formatted-content/?content_format=markdown",
        json={"id": "d1", "title": None, "content": "# Contenu"},
    )

    document = DocsClient(CONFIG).get_document("d1")

    assert document.title == "Sans titre (d1)"
    assert document.content == "# Contenu"


def test_request_raises_docs_error_when_docs_is_unreachable():
    """Connection errors are converted into ``DocsError``."""
    with responses.RequestsMock() as mocked:
        mocked.get(f"{API}/users/me/", body=requests.ConnectionError("refused"))

        with pytest.raises(DocsError, match="not authenticated"):
            DocsClient(CONFIG).ensure_authenticated()
