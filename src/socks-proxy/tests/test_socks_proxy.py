import pytest

from .conftest import SOCKSClient


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
    assert socks_client.test_connection("8.8.8.8", 53), "SOCKS connection should be established successfully"


def test_socks_proxy_connection_refused(socks_client):
    """Test SOCKS proxy behavior when target connection is refused"""
    result = socks_client.test_connection("127.0.0.1", 9999)
    assert not result, "Connection to closed port should fail"


def test_socks_proxy_connection_timeout(socks_client):
    """Test SOCKS proxy timeout behavior"""
    result = socks_client.test_connection("192.0.0.0", 80, timeout=1)
    assert not result, "Connection to non-routable IP should timeout"


def test_socks_proxy_error_handling(socks_client):
    """Test SOCKS proxy error handling"""
    
    assert not socks_client.test_connection("nonexistent.invalid", 80)
