"""Test fixtures for client-bridge tests.

Provides a mock Messages API server and manages the pymap IMAP server
and aiosmtpd SMTP server lifecycle.
"""

import asyncio
import imaplib
import logging
import os
import smtplib
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import jwt as pyjwt
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

IMAP_HOST = os.getenv("IMAP_HOST", "localhost")
IMAP_PORT = int(os.getenv("IMAP_PORT", "1143"))
SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", "1587"))
MOCK_API_PORT = int(os.getenv("MOCK_API_PORT", "8765"))
TEST_API_SECRET = os.getenv("CLIENTBRIDGE_API_SECRET", "test-secret-that-is-at-least-32-bytes")


def _make_sample_eml(
    subject="Test Subject",
    sender="sender@example.com",
    to="recipient@example.com",
    body="This is a test email body.",
    msg_id=None,
):
    """Create a sample EML message."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg["Date"] = "Thu, 06 Mar 2025 12:00:00 +0000"
    msg["Message-ID"] = msg_id or f"<{uuid.uuid4()}@example.com>"
    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(f"<p>{body}</p>", "html"))
    return msg.as_bytes()


class MockMessagesAPI:
    """Mock Messages API server for testing the client-bridge."""

    def __init__(self):
        self.app = FastAPI()
        self.channels: dict[str, dict] = {}
        self.mailbox_threads: dict[str, list[dict]] = {}
        self.thread_messages: dict[str, list[dict]] = {}
        self.message_emls: dict[str, bytes] = {}
        self.submitted_messages: list[dict] = []
        self.server = None

        @self.app.post("/api/v1.0/client-bridge/auth/")
        async def client_bridge_auth(request: Request):
            data = await request.json()
            username = data.get("username")
            password = data.get("password")
            # Find a channel matching this email and password
            for channel_id, channel in self.channels.items():
                if channel.get("mailbox_email") != username:
                    continue
                expected = (channel.get("settings") or {}).get("password")
                if password == expected:
                    expiry = channel.get("token_expiry", 3600)
                    token = pyjwt.encode(
                        {
                            "channel_id": channel_id,
                            "mailbox_id": channel["mailbox_id"],
                            "role": channel.get("role", "sender"),
                            "exp": datetime.now(timezone.utc) + timedelta(seconds=expiry),
                        },
                        TEST_API_SECRET,
                        algorithm="HS256",
                    )
                    return {"token": token}
            return JSONResponse(status_code=401, content={"detail": "Invalid credentials"})

        @self.app.post("/api/v1.0/client-bridge/submit/")
        async def client_bridge_submit(request: Request):
            channel_token = request.headers.get("x-channel-token")
            mail_from = request.headers.get("x-mail-from")
            rcpt_to = request.headers.get("x-rcpt-to")
            body = await request.body()

            self.submitted_messages.append(
                {
                    "channel_token": channel_token,
                    "mail_from": mail_from,
                    "rcpt_to": rcpt_to,
                    "raw_message": body,
                }
            )

            return JSONResponse(
                status_code=202,
                content={
                    "message_id": str(uuid.uuid4()),
                    "status": "accepted",
                },
            )

        @self.app.get("/api/v1.0/threads/")
        async def list_threads(request: Request):
            mailbox_id = request.query_params.get("mailbox_id")
            # Determine folder from query params
            folder = "inbox"
            if request.query_params.get("is_trashed") == "1":
                folder = "trash"
            elif request.query_params.get("is_draft") == "1":
                folder = "drafts"
            elif request.query_params.get("is_spam") == "1":
                folder = "spam"
            elif request.query_params.get("is_archived") == "1":
                folder = "archive"
            elif request.query_params.get("is_sender") == "1":
                folder = "sent"
            elif request.query_params.get("is_starred") == "1":
                folder = "starred"

            key = f"{mailbox_id}:{folder}"
            threads = self.mailbox_threads.get(key, [])
            return {"count": len(threads), "results": threads}

        @self.app.get("/api/v1.0/messages/")
        async def list_messages(request: Request):
            thread_id = request.query_params.get("thread_id")
            messages = self.thread_messages.get(thread_id, [])
            return {"count": len(messages), "results": messages}

        @self.app.get("/api/v1.0/messages/{message_id}/eml")
        async def get_eml(message_id: str):
            eml = self.message_emls.get(message_id)
            if not eml:
                return JSONResponse(status_code=404, content={"detail": "Not found"})
            return Response(
                content=eml,
                media_type="message/rfc822",
                headers={"Content-Disposition": f'attachment; filename="{message_id}.eml"'},
            )

        @self.app.patch("/api/v1.0/messages/{message_id}/")
        async def update_message(message_id: str, request: Request):
            return {"id": message_id}

        @self.app.patch("/api/v1.0/threads/{thread_id}/")
        async def update_thread(thread_id: str, request: Request):
            return {"id": thread_id}

        @self.app.get("/health")
        async def health_check():
            return {"status": "healthy"}

    def add_channel(
        self,
        channel_id: str,
        mailbox_id: str,
        password: str,
        mailbox_email: str = "test@example.com",
        token_expiry: int = 3600,
        role: str = "sender",
    ):
        """Register a channel for authentication."""
        self.channels[channel_id] = {
            "mailbox_id": mailbox_id,
            "mailbox_email": mailbox_email,
            "settings": {"password": password},
            "token_expiry": token_expiry,
            "role": role,
        }

    def add_thread(self, mailbox_id: str, folder: str, thread: dict):
        """Add a thread to a mailbox folder."""
        key = f"{mailbox_id}:{folder}"
        self.mailbox_threads.setdefault(key, []).append(thread)

    def add_message(self, thread_id: str, message: dict, eml: bytes | None = None):
        """Add a message to a thread with optional EML content."""
        self.thread_messages.setdefault(thread_id, []).append(message)
        if eml is not None:
            self.message_emls[message["id"]] = eml

    def start(self):
        self.server = uvicorn.Server(
            uvicorn.Config(
                self.app,
                host="0.0.0.0",
                port=MOCK_API_PORT,
                log_level="info",
                loop="asyncio",
                reload=False,
            )
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        # Wait for server to be ready
        for _ in range(50):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.connect(("localhost", MOCK_API_PORT))
                break
            except (ConnectionRefusedError, OSError):
                time.sleep(0.1)
        else:
            raise RuntimeError(f"Mock API server did not become ready on port {MOCK_API_PORT}")

    def stop(self):
        if self.server:
            self.server.should_exit = True
            self.thread.join(timeout=10)


class IMAPServer:
    """Manages a pymap IMAP server for testing."""

    def __init__(self, api_url: str, api_secret: str = ""):
        self.api_url = api_url
        self.api_secret = api_secret
        self._process = None
        self._loop = None
        self._thread = None

    def start(self):
        """Start the pymap server in a background thread."""

        def run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._run_server())

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

        # Wait for IMAP port to be ready
        for _ in range(100):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1)
                    s.connect((IMAP_HOST, IMAP_PORT))
                logger.info("IMAP server ready on %s:%d", IMAP_HOST, IMAP_PORT)
                return
            except (ConnectionRefusedError, OSError, socket.timeout):
                time.sleep(0.1)
        raise RuntimeError("IMAP server failed to start")

    async def _run_server(self):
        from argparse import Namespace

        from pymap.backend import backends
        from pymap.service import services

        # Register our backend
        from src.backend import MessagesBackend

        backends.add("messages-api", MessagesBackend)

        args = Namespace(
            host=IMAP_HOST,
            port=IMAP_PORT,
            debug=True,
            cert=None,
            key=None,
            tls=False,
            passlib_cfg=None,
            proxy_protocol=None,
            inherited_sockets=None,
            api_url=self.api_url,
            api_secret=self.api_secret,
            backend="messages-api",
        )

        from contextlib import AsyncExitStack

        backend, config = await MessagesBackend.init(args)
        config.apply_context()

        service_types = list(services.values())
        svc_instances = [svc_type(backend, config) for svc_type in service_types]

        async with AsyncExitStack() as stack:
            await backend.start(stack)
            for service in svc_instances:
                await service.start(stack)
            # Run forever (until thread is killed)
            try:
                while True:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass

    def stop(self):
        if self._loop:
            for task in asyncio.all_tasks(self._loop):
                self._loop.call_soon_threadsafe(task.cancel)
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread:
                self._thread.join(timeout=5)


class SMTPServer:
    """Manages an aiosmtpd SMTP server for testing."""

    def __init__(self, api_url: str, api_secret: str = ""):
        self.api_url = api_url
        self.api_secret = api_secret
        self._controller = None

    def start(self):
        """Start the SMTP submission server in a background thread."""
        from aiosmtpd.controller import Controller

        from src.api.client import MessagesAPIClient
        from src.submission import SubmissionAuthenticator, SubmissionHandler

        api_client = MessagesAPIClient(self.api_url, api_secret=self.api_secret)
        handler = SubmissionHandler(api_client)
        authenticator = SubmissionAuthenticator(api_client)

        self._controller = Controller(
            handler,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            authenticator=authenticator,
            auth_require_tls=False,
            auth_required=True,
        )
        self._controller.start()

        # Wait for SMTP port to be ready
        for _ in range(100):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1)
                    s.connect((SMTP_HOST, SMTP_PORT))
                logger.info("SMTP server ready on %s:%d", SMTP_HOST, SMTP_PORT)
                return
            except (ConnectionRefusedError, OSError, socket.timeout):
                time.sleep(0.1)
        raise RuntimeError("SMTP server failed to start")

    def stop(self):
        if self._controller:
            self._controller.stop()


# --- Fixtures ---


@pytest.fixture(scope="session")
def mock_api():
    """Session-scoped mock API server."""
    server = MockMessagesAPI()
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def imap_server(mock_api):
    """Session-scoped IMAP server connected to the mock API."""
    server = IMAPServer(f"http://localhost:{MOCK_API_PORT}/api/v1.0/", api_secret=TEST_API_SECRET)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def smtp_server(mock_api):
    """Session-scoped SMTP server connected to the mock API."""
    server = SMTPServer(f"http://localhost:{MOCK_API_PORT}/api/v1.0/", api_secret=TEST_API_SECRET)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def test_channel(mock_api):
    """A test channel with messages set up in the mock API."""
    channel_id = "00000000-0000-4000-a000-000000000001"
    mailbox_id = "00000000-0000-4000-a000-000000000002"
    mailbox_email = "test@example.com"
    password = "test-app-password-123"

    mock_api.add_channel(channel_id, mailbox_id, password, mailbox_email=mailbox_email)

    # Create threads and messages for INBOX
    thread1_id = str(uuid.uuid4())
    msg1_id = str(uuid.uuid4())
    msg1_eml = _make_sample_eml(
        subject="Welcome to Messages",
        sender="admin@example.com",
        to="test@example.com",
        body="Welcome! This is your first message.",
    )
    mock_api.add_thread(
        mailbox_id,
        "inbox",
        {
            "id": thread1_id,
            "subject": "Welcome to Messages",
            "snippet": "Welcome! This is your first message.",
            "messaged_at": "2025-03-06T12:00:00Z",
        },
    )
    mock_api.add_message(
        thread1_id,
        {
            "id": msg1_id,
            "thread_id": thread1_id,
            "subject": "Welcome to Messages",
            "is_unread": True,
            "is_starred": False,
            "is_trashed": False,
            "sent_at": "2025-03-06T12:00:00Z",
            "created_at": "2025-03-06T12:00:00Z",
        },
        msg1_eml,
    )

    # Second thread with a read + starred message
    thread2_id = str(uuid.uuid4())
    msg2_id = str(uuid.uuid4())
    msg2_eml = _make_sample_eml(
        subject="Important Update",
        sender="boss@example.com",
        to="test@example.com",
        body="Please review the attached document.",
    )
    mock_api.add_thread(
        mailbox_id,
        "inbox",
        {
            "id": thread2_id,
            "subject": "Important Update",
            "snippet": "Please review the attached document.",
            "messaged_at": "2025-03-06T13:00:00Z",
        },
    )
    mock_api.add_message(
        thread2_id,
        {
            "id": msg2_id,
            "thread_id": thread2_id,
            "subject": "Important Update",
            "is_unread": False,
            "is_starred": True,
            "is_trashed": False,
            "sent_at": "2025-03-06T13:00:00Z",
            "created_at": "2025-03-06T13:00:00Z",
        },
        msg2_eml,
    )

    # Add a message to Sent folder
    thread3_id = str(uuid.uuid4())
    msg3_id = str(uuid.uuid4())
    msg3_eml = _make_sample_eml(
        subject="Re: Project Update",
        sender="test@example.com",
        to="colleague@example.com",
        body="Here is the project update you requested.",
    )
    mock_api.add_thread(
        mailbox_id,
        "sent",
        {
            "id": thread3_id,
            "subject": "Re: Project Update",
            "snippet": "Here is the project update you requested.",
            "messaged_at": "2025-03-06T14:00:00Z",
        },
    )
    mock_api.add_message(
        thread3_id,
        {
            "id": msg3_id,
            "thread_id": thread3_id,
            "subject": "Re: Project Update",
            "is_unread": False,
            "is_starred": False,
            "is_trashed": False,
            "is_sender": True,
            "sent_at": "2025-03-06T14:00:00Z",
            "created_at": "2025-03-06T14:00:00Z",
        },
        msg3_eml,
    )

    # Add a message to Trash
    thread4_id = str(uuid.uuid4())
    msg4_id = str(uuid.uuid4())
    msg4_eml = _make_sample_eml(
        subject="Old Newsletter",
        sender="news@example.com",
        to="test@example.com",
        body="This is an old newsletter.",
    )
    mock_api.add_thread(
        mailbox_id,
        "trash",
        {
            "id": thread4_id,
            "subject": "Old Newsletter",
            "snippet": "This is an old newsletter.",
            "messaged_at": "2025-03-05T10:00:00Z",
        },
    )
    mock_api.add_message(
        thread4_id,
        {
            "id": msg4_id,
            "thread_id": thread4_id,
            "subject": "Old Newsletter",
            "is_unread": True,
            "is_starred": False,
            "is_trashed": True,
            "sent_at": "2025-03-05T10:00:00Z",
            "created_at": "2025-03-05T10:00:00Z",
        },
        msg4_eml,
    )

    return {
        "channel_id": channel_id,
        "mailbox_id": mailbox_id,
        "mailbox_email": mailbox_email,
        "password": password,
        "inbox_message_count": 2,
        "sent_message_count": 1,
        "trash_message_count": 1,
    }


@pytest.fixture
def imap_client(imap_server, test_channel):
    """An authenticated IMAP client connected to the test server."""
    client = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
    client.login(test_channel["mailbox_email"], test_channel["password"])
    yield client
    try:
        client.logout()
    except Exception:
        pass


@pytest.fixture
def imap_connection(imap_server):
    """An unauthenticated IMAP connection."""
    client = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
    yield client
    try:
        client.logout()
    except Exception:
        pass


@pytest.fixture
def smtp_client(smtp_server, test_channel):
    """An authenticated SMTP client connected to the test server."""
    client = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
    client.ehlo()
    client.login(test_channel["mailbox_email"], test_channel["password"])
    yield client
    try:
        client.quit()
    except Exception:
        pass


@pytest.fixture
def smtp_connection(smtp_server):
    """An unauthenticated SMTP connection."""
    client = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
    client.ehlo()
    yield client
    try:
        client.quit()
    except Exception:
        pass


# Token expires in 2 seconds — short enough for fast tests
EXPIRY_SECONDS = 2


@pytest.fixture(scope="session")
def expiring_channel(mock_api):
    """A test channel whose session tokens expire in EXPIRY_SECONDS."""
    channel_id = "00000000-0000-4000-a000-000000000099"
    mailbox_id = "00000000-0000-4000-a000-000000000098"
    mailbox_email = "expiring@example.com"
    password = "expiring-password"

    mock_api.add_channel(
        channel_id,
        mailbox_id,
        password,
        mailbox_email=mailbox_email,
        token_expiry=EXPIRY_SECONDS,
    )

    # Add a thread so SELECT INBOX triggers an API call
    thread_id = str(uuid.uuid4())
    msg_id = str(uuid.uuid4())
    mock_api.add_thread(
        mailbox_id,
        "inbox",
        {
            "id": thread_id,
            "subject": "Expiry test",
            "messaged_at": "2025-03-06T12:00:00Z",
        },
    )
    mock_api.add_message(
        thread_id,
        {
            "id": msg_id,
            "thread_id": thread_id,
            "subject": "Expiry test",
            "is_unread": True,
            "is_starred": False,
            "is_trashed": False,
            "sent_at": "2025-03-06T12:00:00Z",
            "created_at": "2025-03-06T12:00:00Z",
        },
    )

    return {
        "channel_id": channel_id,
        "mailbox_id": mailbox_id,
        "mailbox_email": mailbox_email,
        "password": password,
    }
