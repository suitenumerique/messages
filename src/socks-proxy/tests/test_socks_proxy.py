import pytest
import logging
import socket
import socks
import time

logger = logging.getLogger(__name__)

from .conftest import SOCKSClient, PROXY1_CONFIG


# Authentication Tests
@pytest.mark.parametrize("proxy_fixture", ["socks_client", "socks_client_proxy2"])
def test_socks_authentication_success(request, proxy_fixture):
    """Test successful SOCKS authentication with correct credentials on both proxies"""
    socks_client = request.getfixturevalue(proxy_fixture)
    result = socks_client.test_connection("8.8.8.8", 53)
    assert result, f"SOCKS connection should succeed with {proxy_fixture}"


def test_socks_authentication_invalid_password(socks_client):
    """Test failed SOCKS authentication with incorrect password"""
    # Create client with wrong password using existing fixture
    client = SOCKSClient(
        proxy_host=socks_client.proxy_host,
        proxy_port=socks_client.proxy_port,
        username=socks_client.username,
        password="wrong_password"
    )
    
    result = client.test_connection("8.8.8.8", 53)
    assert not result, "SOCKS connection should fail with invalid password"


def test_socks_authentication_invalid_username(socks_client):
    """Test failed SOCKS authentication with incorrect username"""
    # Create client with wrong username using existing fixture
    client = SOCKSClient(
        proxy_host=socks_client.proxy_host,
        proxy_port=socks_client.proxy_port,
        username="wrong_username",
        password=socks_client.password
    )
    
    result = client.test_connection("8.8.8.8", 53)
    assert not result, "SOCKS connection should fail with invalid username"


def test_socks_authentication_no_credentials(socks_client):
    """Test SOCKS connection without authentication (should fail)"""
    # Create client without credentials using existing fixture
    client = SOCKSClient(
        proxy_host=socks_client.proxy_host,
        proxy_port=socks_client.proxy_port
    )
    
    result = client.test_connection("8.8.8.8", 53)
    assert not result, "SOCKS connection should fail without credentials"


def test_socks_authentication_failure_handling(socks_client):
    """Test SOCKS proxy behavior with invalid authentication"""
    # Use existing fixture but override with invalid credentials
    client = SOCKSClient(
        proxy_host=socks_client.proxy_host,
        proxy_port=socks_client.proxy_port,
        username="invalid_user",
        password="invalid_pass"
    )
    
    result = client.test_connection("8.8.8.8", 53)
    assert not result, "Connection should fail with invalid credentials"


# Connection Tests
def test_socks_proxy_connection_establishment(socks_client):
    """Test that SOCKS proxy connection can be established"""
    try:
        sock = socks_client.get_socket(timeout=5)

        sock.connect(("8.8.8.8", 53))
        sock.close()
        assert True, "SOCKS connection should be established successfully"
    except Exception as e:
        pytest.fail(f"Failed to establish SOCKS connection: {e}")


def test_socks_proxy_connection_refused(socks_client):
    """Test SOCKS proxy behavior when target connection is refused"""
    result = socks_client.test_connection("127.0.0.1", 9999)
    assert not result, "Connection to closed port should fail"


def test_socks_proxy_connection_timeout(socks_client):
    """Test SOCKS proxy timeout behavior"""
    result = socks_client.test_connection("192.0.2.1", 80, timeout=1)
    assert not result, "Connection to non-routable IP should timeout"


# def test_socks_proxy_multiple_connections(socks_client):
#     """Test multiple simultaneous SOCKS connections"""
#     connections = []
#     try:
#         for i in range(3):
#             sock = socks.socksocket()
#             if socks_client.username and socks_client.password:
#                 sock.set_proxy(
#                     socks.SOCKS5,
#                     socks_client.proxy_host,
#                     socks_client.proxy_port,
#                     username=socks_client.username,
#                     password=socks_client.password
#                 )
#             else:
#                 sock.set_proxy(socks.SOCKS5, socks_client.proxy_host, socks_client.proxy_port)
            
#             sock.settimeout(5)
#             sock.connect(("8.8.8.8", 53))
#             connections.append(sock)
        
#         # Verify all connections work
#         for sock in connections:
#             sock.send(b"\x00\x01\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x07example\x03com\x00\x00\x01\x00\x01")
#             response = sock.recv(1024)
#             assert len(response) > 0, "DNS query should receive response"
            
#     except Exception as e:
#         pytest.fail(f"Multiple connections test failed: {e}")
#     finally:
#         for sock in connections:
#             try:
#                 sock.close()
#             except:
#                 pass


def test_socks_proxy_error_handling(socks_client):
    """Test SOCKS proxy error handling"""
    try:
        sock = socks_client.get_socket()

        sock.connect(("invalid.ip.address", 80))
        pytest.fail("Should not be able to connect to invalid IP")
    except (socket.gaierror, socks.ProxyConnectionError, socks.GeneralProxyError):
        pass
    except Exception as e:
        pytest.fail(f"Unexpected error type: {type(e).__name__}")

