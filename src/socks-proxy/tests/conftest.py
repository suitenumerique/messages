import pytest
import smtplib
import time
import logging
import os
import socket
import subprocess
import ssl
import struct
import socks
from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Message
from email.parser import BytesParser

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Parse SOCKS proxy environment variables
from urllib.parse import urlparse
from dataclasses import dataclass

@dataclass
class ProxyConfig:
    username: str = None
    password: str = None
    host: str = "localhost"
    port: int = 1080

def parse_proxy_env(proxy_env):
    """Parse SOCKS_PROXY1 or SOCKS_PROXY2 environment variable
    Format: username:password@host:port
    """
    if not proxy_env:
        return ProxyConfig()
    
    try:
        # Add scheme to make it a valid URL for urlparse
        if not proxy_env.startswith(('http://', 'https://', 'socks://')):
            proxy_env = f"socks://{proxy_env}"
        
        parsed = urlparse(proxy_env)
        
        return ProxyConfig(
            username=parsed.username,
            password=parsed.password,
            host=parsed.hostname or "localhost",
            port=parsed.port or 1080
        )
    except Exception:
        return ProxyConfig()

# Parse both proxy configurations
PROXY1_CONFIG = parse_proxy_env(os.getenv("SOCKS_PROXY1"))
PROXY2_CONFIG = parse_proxy_env(os.getenv("SOCKS_PROXY2"))


def get_container_ip():
    """Get the container's IP address automatically"""
    try:
        result = subprocess.run(['hostname', '-I'], capture_output=True, text=True)
        # hostname -I returns space-separated IPs, first one is usually the main one
        return result.stdout.strip().split()[0]
    except:
        return "127.0.0.1"  # fallback


class MessageStore:
    """Simple storage for received email messages"""
    def __init__(self):
        self.messages = []

    def add_message(self, message_data):
        self.messages.append(message_data)

    def clear(self):
        self.messages.clear()

    def get_messages(self):
        return self.messages
    
    def get_last_connection_info(self):
        """Get connection info from the last received message"""
        if self.messages:
            return self.messages[-1].get("connection_info", {})
        return {}
    
    def get_connection_info_for_subject(self, subject):
        """Get connection info for a specific message subject"""
        for message in self.messages:
            if message.get("subject") == subject:
                return message.get("connection_info", {})
        return {}


class MockSMTPHandler(Message):
    """Handle SMTP messages and store them"""
    def __init__(self, message_store):
        super().__init__()
        self.message_store = message_store

    async def handle_DATA(self, server, session, envelope):
        message = self.prepare_message(session, envelope)
        # Capture connection info from session
        connection_info = {
            "peer_host": (session.peer or [None, None])[0],
            "peer_port": (session.peer or [None, None])[1],
        }
        self.handle_message(message, envelope.content, connection_info)
        return "250 OK"

    def handle_message(self, message, raw_message, connection_info):
        parsed_message = BytesParser().parsebytes(message.as_bytes())
        message_data = {
            "from": parsed_message.get("From"),
            "to": parsed_message.get("To"),
            "subject": parsed_message.get("Subject"),
            "raw_message": raw_message,
            "connection_info": connection_info,
        }
        self.message_store.add_message(message_data)


class MockSMTPServer:
    """Mock SMTP server for testing"""
    def __init__(self, host="0.0.0.0", port=2525):
        self.host = host
        self.port = port
        self.message_store = MessageStore()
        handler = MockSMTPHandler(self.message_store)
        self.controller = Controller(handler, hostname=self.host, port=self.port)

    def start(self):
        self.controller.start()

    def stop(self):
        self.controller.stop()

    def clear_messages(self):
        self.message_store.clear()

    def get_messages(self):
        return self.message_store.get_messages()


def create_proxied_socket(proxy_host, proxy_port, target_host, target_port, username=None, password=None, timeout=5):
    """Create a socket connected through a SOCKS proxy"""
    proxy = socks.socksocket()
    if type(timeout) in {int, float}:
        proxy.settimeout(timeout)
    proxy.set_proxy(socks.PROXY_TYPE_SOCKS5, proxy_host, proxy_port, rdns=False, username=username, password=password)
    proxy.connect((target_host, target_port))
    
    return proxy

class SOCKSClient:
    """SOCKS client for testing"""
    def __init__(self, proxy_host, proxy_port, username=None, password=None):
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.username = username
        self.password = password

    def test_connection(self, target_host, target_port, timeout=5):
        try:
            sock = create_proxied_socket(
                self.proxy_host,
                self.proxy_port,
                target_host,
                target_port,
                self.username,
                self.password,
                timeout
            )
            sock.close()
            return True
        except Exception:
            return False


@pytest.fixture(scope="session")
def mock_smtp_server():
    server = MockSMTPServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture
def socks_client():
    return SOCKSClient(
        proxy_host=PROXY1_CONFIG.host,
        proxy_port=PROXY1_CONFIG.port,
        username=PROXY1_CONFIG.username,
        password=PROXY1_CONFIG.password
    )


@pytest.fixture
def socks_client_proxy2():
    """SOCKS client using PROXY2 configuration"""
    return SOCKSClient(
        proxy_host=PROXY2_CONFIG.host,
        proxy_port=PROXY2_CONFIG.port,
        username=PROXY2_CONFIG.username,
        password=PROXY2_CONFIG.password
    )


@pytest.fixture
def smtp_client_direct():
    client = smtplib.SMTP("localhost", 2525)
    client.set_debuglevel(2)

    yield client

    try:
        client.quit()
    except:
        pass


class ProxySMTP(smtplib.SMTP):
    def __init__(self, *args, **kwargs):
        if "socks_client" in kwargs:
            self.socks_client = kwargs.pop("socks_client")
        super().__init__(*args, **kwargs)

    def _get_socket(self, host, port, timeout):
        # This makes it simpler for SMTP_SSL to use the SMTP connect code
        # and just alter the socket connection bit.
        if timeout is not None and not timeout:
            raise ValueError('Non-blocking socket (timeout=0) is not supported')
        if self.debuglevel > 0:
            self._print_debug('connect: to', (host, port), self.source_address)

        return create_proxied_socket(
            self.socks_client.proxy_host,
            self.socks_client.proxy_port,
            host,
            port,
            self.socks_client.username,
            self.socks_client.password,
            timeout
        )


@pytest.fixture
def smtp_client_via_proxy(socks_client):
    # Create SMTP client that connects through SOCKS proxy to the container's IP

    container_ip = get_container_ip()
    print(f"Container IP: {container_ip}")

    client = ProxySMTP(container_ip, 2525, socks_client=socks_client)
    client.set_debuglevel(2)
    
    yield client

    try:
        client.quit()
    except:
        pass

