#  Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import pytest
from smithy_core.aio.interfaces import ErrorInfo

try:
    import aiohttp
    from smithy_http.aio.aiohttp import AIOHTTPClient

    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

try:
    from smithy_http.aio.crt import AWSCRTHTTPClient

    HAS_CRT = True
except ImportError:
    HAS_CRT = False


class TestTimeoutErrorHandling:
    """Test timeout error handling for HTTP clients."""

    @pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not available")
    def test_aiohttp_timeout_error_detection(self):
        """Test AIOHTTPClient timeout error detection."""
        client = AIOHTTPClient()

        timeout_err = TimeoutError("Connection timed out")
        result = client.get_error_info(timeout_err)
        assert result == ErrorInfo(is_timeout_error=True, fault="client")

        other_err = ValueError("Not a timeout")
        result = client.get_error_info(other_err)
        assert result == ErrorInfo(is_timeout_error=False)
        assert result.fault is None

    @pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not available")
    def test_aiohttp_server_timeout_by_name(self):
        """Test aiohttp ServerTimeoutError detection by class name."""
        client = AIOHTTPClient()

        server_err = aiohttp.ServerTimeoutError("Server timeout")
        result = client.get_error_info(server_err)
        assert result == ErrorInfo(is_timeout_error=True, fault="server")

    @pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not available")
    def test_aiohttp_connection_timeout_by_name(self):
        """Test aiohttp ConnectionTimeoutError detection by class name."""
        client = AIOHTTPClient()

        conn_err = aiohttp.ConnectionTimeoutError("Connection timeout")
        result = client.get_error_info(conn_err)
        assert result == ErrorInfo(is_timeout_error=True, fault="client")

    @pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not available")
    def test_aiohttp_socket_timeout_by_name(self):
        """Test aiohttp SocketTimeoutError detection by class name."""
        client = AIOHTTPClient()

        socket_err = aiohttp.SocketTimeoutError("Socket timeout")
        result = client.get_error_info(socket_err)
        assert result == ErrorInfo(is_timeout_error=True, fault="client")

    @pytest.mark.skipif(not HAS_CRT, reason="awscrt not available")
    def test_crt_timeout_error_detection(self):
        """Test CRTClient timeout error detection."""
        client = AWSCRTHTTPClient()

        timeout_err = TimeoutError("Connection timed out")
        result = client.get_error_info(timeout_err)
        assert result == ErrorInfo(is_timeout_error=True, fault="client")
        
        other_err = ValueError("Not a timeout")
        result = client.get_error_info(other_err)
        assert result == ErrorInfo(is_timeout_error=False)
        assert result.fault is None
