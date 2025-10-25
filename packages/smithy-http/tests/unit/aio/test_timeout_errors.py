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

    def test_timeout_error_detection(self):
        """Test timeout error detection for standard TimeoutError."""
        timeout_err = TimeoutError("Connection timed out")
        
        if HAS_AIOHTTP:
            result = AIOHTTPClient.get_error_info(None, timeout_err)
            assert result == ErrorInfo(is_timeout_error=True, fault="client")
        
        if HAS_CRT:
            result = AWSCRTHTTPClient.get_error_info(None, timeout_err)
            assert result == ErrorInfo(is_timeout_error=True, fault="client")

    def test_non_timeout_error_detection(self):
        """Test non-timeout error detection."""
        other_err = ValueError("Not a timeout")
        
        if HAS_AIOHTTP:
            result = AIOHTTPClient.get_error_info(None, other_err)
            assert result == ErrorInfo(is_timeout_error=False)
            assert result.fault == "client"  # Default fault is "client"
        
        if HAS_CRT:
            result = AWSCRTHTTPClient.get_error_info(None, other_err)
            assert result == ErrorInfo(is_timeout_error=False)
            assert result.fault == "client"  # Default fault is "client"

    @pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not available")
    def test_aiohttp_server_timeout_by_name(self):
        """Test aiohttp ServerTimeoutError detection by class name."""
        server_err = aiohttp.ServerTimeoutError("Server timeout")
        result = AIOHTTPClient.get_error_info(None, server_err)
        assert result == ErrorInfo(is_timeout_error=True, fault="server")

    @pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not available")
    def test_aiohttp_connection_timeout_by_name(self):
        """Test aiohttp ConnectionTimeoutError detection by class name."""
        conn_err = aiohttp.ConnectionTimeoutError("Connection timeout")
        result = AIOHTTPClient.get_error_info(None, conn_err)
        assert result == ErrorInfo(is_timeout_error=True, fault="client")
