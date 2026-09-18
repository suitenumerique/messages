"""Read-only client for the Docs internal API, authenticated as a Docs user.

Docs has no service account: the client logs in like a browser (Keycloak login
form, then the ``docs_sessionid`` cookie), or reuses a session cookie copied
from a browser.
"""

import html
import logging
import re
import urllib.parse
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from django.conf import settings

import requests

logger = logging.getLogger(__name__)

LOCAL_HOSTS = {"localhost", "127.0.0.1"}
LOGIN_FORM_ACTION_RE = re.compile(r'<form[^>]*action="([^"]+)"')
MAX_REDIRECTS = 10
UNTITLED_DOCUMENT_TITLE = "Sans titre"


class DocsError(Exception):
    """Docs is unreachable, refused the credentials or returned an error."""


@dataclass(frozen=True)
class DocsDocument:
    """A Docs document converted to Markdown."""

    id: str
    title: str
    content: str


class DocsClient:
    """Browse and read every document the authenticated Docs user can access."""

    def __init__(self, config: dict | None = None, session_id: str | None = None):
        config = config or settings.DOCS_CONFIG
        self.base_url = config["base_url"].rstrip("/")
        self.network_host = config.get("network_host")
        self.api_prefix = config["api_prefix"]
        self.page_size = config["page_size"]
        self.max_documents = config["max_documents"]
        self.timeout = config["timeout_seconds"]
        # Cookies are kept per netloc: the Keycloak cookies carry attributes
        # (Secure, Version=1) that http.cookiejar refuses to send over plain
        # HTTP, which breaks the local login.
        self._cookies: dict[str, dict[str, str]] = {}
        if session_id:
            netloc = urllib.parse.urlsplit(self.base_url).netloc
            self._cookies[netloc] = {config["session_cookie_name"]: session_id}

    def login(self, email: str, password: str) -> None:
        """Open a Docs session by submitting the Keycloak login form."""
        login_page = self._request("GET", self._api_url("/authenticate/"))
        match = LOGIN_FORM_ACTION_RE.search(login_page.text)
        if not match:
            raise DocsError("Keycloak login form not found.")

        response = self._request(
            "POST",
            html.unescape(match.group(1)),
            data={"username": email, "password": password},
            # Once back on Docs, the session is open: the final redirect to the
            # frontend is not needed (and the frontend may not be running).
            should_follow=lambda next_url: (
                "/realms/" in next_url or next_url.startswith(f"{self.base_url}/")
            ),
        )
        # A refused login renders the Keycloak form again instead of redirecting.
        if "/realms/" in response.url:
            raise DocsError("Docs login refused: invalid email or password.")

    def ensure_authenticated(self) -> None:
        """Raise ``DocsError`` if the session is not authenticated."""
        try:
            self._get_json(self._api_url("/users/me/"))
        except DocsError as exc:
            raise DocsError(
                "Docs session is not authenticated (expired or invalid cookie)."
            ) from exc

    def iter_document_ids(self) -> Iterator[str]:
        """Yield the ids of all readable documents, sub-pages included."""
        pending = deque([self._api_url("/documents/")])
        seen: set[str] = set()

        while pending:
            url = self._with_page_size(pending.popleft())
            while url:
                page = self._get_json(url)
                for document in page.get("results", []):
                    if document["id"] in seen:
                        continue
                    if len(seen) >= self.max_documents:
                        logger.warning(
                            "More than %d Docs documents, the others are ignored",
                            self.max_documents,
                        )
                        return
                    seen.add(document["id"])
                    yield document["id"]
                    if document.get("numchild"):
                        pending.append(
                            self._api_url(f"/documents/{document['id']}/children/")
                        )
                url = page.get("next")

    def get_document(self, document_id: str) -> DocsDocument:
        """Return a document with its content converted to Markdown."""
        data = self._get_json(
            self._api_url(
                f"/documents/{document_id}/formatted-content/?content_format=markdown"
            )
        )
        title = (data.get("title") or "").strip()
        return DocsDocument(
            id=str(data["id"]),
            title=title or f"{UNTITLED_DOCUMENT_TITLE} ({data['id']})",
            content=data.get("content") or "",
        )

    def _api_url(self, path: str) -> str:
        return f"{self.base_url}{self.api_prefix}{path}"

    def _with_page_size(self, url: str) -> str:
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}page_size={self.page_size}"

    def _get_json(self, url: str) -> dict:
        response = self._request("GET", url)
        try:
            return response.json()
        except ValueError as exc:
            raise DocsError(f"Docs returned a non-JSON response on {url}") from exc

    def _request(
        self,
        method: str,
        url: str,
        data: dict | None = None,
        should_follow: Callable[[str], bool] | None = None,
    ) -> requests.Response:
        """Send a request, following redirects and keeping cookies per host.

        ``should_follow(next_url)`` returning False stops on the redirect
        response. Its ``url`` is always the public URL, not the network one.
        """
        for _ in range(MAX_REDIRECTS):
            netloc = urllib.parse.urlsplit(url).netloc
            cookies = self._cookies.setdefault(netloc, {})
            headers = {"Host": netloc}
            if cookies:
                headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())

            try:
                response = requests.request(
                    method,
                    self._network_url(url),
                    headers=headers,
                    data=data,
                    allow_redirects=False,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise DocsError(f"Docs unreachable ({netloc}): {exc}") from exc

            self._store_cookies(netloc, response)
            response.url = url
            if response.status_code >= 400:
                raise DocsError(f"HTTP {response.status_code} on {url}")
            if not response.is_redirect:
                return response

            next_url = urllib.parse.urljoin(url, response.headers["Location"])
            if should_follow and not should_follow(next_url):
                return response
            url, method, data = next_url, "GET", None

        raise DocsError(f"Too many redirects from {url}")

    def _network_url(self, url: str) -> str:
        """Send requests aimed at localhost to ``network_host`` when set."""
        parts = urllib.parse.urlsplit(url)
        if not self.network_host or parts.hostname not in LOCAL_HOSTS:
            return url
        netloc = self.network_host
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        return urllib.parse.urlunsplit(parts._replace(netloc=netloc))

    def _store_cookies(self, netloc: str, response) -> None:
        cookies = self._cookies.setdefault(netloc, {})
        for header in response.raw.headers.getlist("Set-Cookie"):
            name, _, rest = header.partition("=")
            value = rest.split(";", 1)[0]
            if value and "max-age=0" not in header.lower():
                cookies[name.strip()] = value
            else:
                cookies.pop(name.strip(), None)
