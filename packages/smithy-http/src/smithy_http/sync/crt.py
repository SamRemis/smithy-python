#  Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
#  pyright: reportMissingTypeStubs=false,reportUnknownMemberType=false
import threading
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING, Any

from awscrt.exceptions import AwsCrtError

if TYPE_CHECKING:
    from awscrt import http as crt_http
    from awscrt import io as crt_io
    from awscrt.http import HttpClientConnection, HttpClientStream

try:
    from awscrt import http as crt_http
    from awscrt import io as crt_io

    HAS_CRT = True
except ImportError:
    HAS_CRT = False  # type: ignore

from smithy_core import interfaces as core_interfaces
from smithy_core.exceptions import MissingDependencyError
from smithy_core.interfaces import StreamingBlob

from .. import Field, Fields
from .. import interfaces as http_interfaces
from ..exceptions import SmithyHTTPError
from . import interfaces as http_sync_interfaces

# Default buffer size for reading from streams (8 KB)
DEFAULT_READ_BUFFER_SIZE = 8192


def _assert_crt() -> None:
    if not HAS_CRT:
        raise MissingDependencyError(
            "Attempted to use awscrt component, but awscrt is not installed."
        )


class _CRTEventLoop:
    def __init__(self) -> None:
        _assert_crt()
        self.bootstrap = self._initialize_default_loop()

    def _initialize_default_loop(self) -> "crt_io.ClientBootstrap":
        event_loop_group = crt_io.EventLoopGroup(1)
        host_resolver = crt_io.DefaultHostResolver(event_loop_group)
        return crt_io.ClientBootstrap(event_loop_group, host_resolver)


class AWSCRTHTTPResponse(http_sync_interfaces.HTTPResponse):
    """Sync CRT HTTP response with body already buffered."""

    def __init__(
        self,
        *,
        status: int,
        fields: Fields,
        body: bytes = b"",
    ) -> None:
        self._status = status
        self._fields = fields
        self._body = body

    @property
    def status(self) -> int:
        return self._status

    @property
    def fields(self) -> Fields:
        return self._fields

    @property
    def body(self) -> bytes:
        return self._body

    @property
    def reason(self) -> str | None:
        return None

    def consume_body(self) -> bytes:
        return self._body

    def __repr__(self) -> str:
        return (
            f"AWSCRTHTTPResponse("
            f"status={self.status}, "
            f"fields={self.fields!r}, body_len={len(self._body)})"
        )


ConnectionPoolKey = tuple[str, str, int | None]
ConnectionPoolDict = dict[ConnectionPoolKey, "HttpClientConnection"]


@dataclass(kw_only=True)
class AWSCRTHTTPClientConfig(http_interfaces.HTTPClientConfiguration):
    """AWS CRT HTTP client configuration for sync usage.

    :param read_buffer_size: The buffer size in bytes to use when reading from streams.
        Defaults to 8192 (8 KB).
    """

    read_buffer_size: int = DEFAULT_READ_BUFFER_SIZE

    def __post_init__(self) -> None:
        _assert_crt()


class _CRTTimeoutError(Exception):
    """Internal wrapper for CRT timeout errors."""


class AWSCRTHTTPClient(http_sync_interfaces.HTTPClient):
    """Synchronous HTTP client using AWS CRT with blocking Future.result() calls."""

    _HTTP_PORT = 80
    _HTTPS_PORT = 443
    _TIMEOUT_ERROR_NAMES = frozenset(["AWS_IO_SOCKET_TIMEOUT", "AWS_IO_SOCKET_CLOSED"])

    TIMEOUT_EXCEPTIONS = (_CRTTimeoutError,)

    def __init__(
        self,
        eventloop: _CRTEventLoop | None = None,
        client_config: AWSCRTHTTPClientConfig | None = None,
    ) -> None:
        _assert_crt()
        self._config = client_config or AWSCRTHTTPClientConfig()
        if eventloop is None:
            eventloop = _CRTEventLoop()
        self._eventloop = eventloop
        self._client_bootstrap = self._eventloop.bootstrap
        self._tls_ctx = crt_io.ClientTlsContext(crt_io.TlsContextOptions())
        self._socket_options = crt_io.SocketOptions()
        self._connections: ConnectionPoolDict = {}

    def send(
        self,
        request: http_sync_interfaces.HTTPRequest,
        *,
        request_config: http_sync_interfaces.HTTPRequestConfiguration | None = None,
    ) -> AWSCRTHTTPResponse:
        """Send HTTP request using awscrt client (blocking).

        :param request: The request including destination URI, fields, payload.
        :param request_config: Configuration specific to this request.
        """
        try:
            crt_request = self._marshal_request(request)
            connection = self._get_connection(request.destination)

            if (body_stream := self._create_body_stream(request.body)) is not None:
                crt_request.body_stream = body_stream

            status_holder: list[int] = [0]
            response_headers: list[tuple[str, str]] = []
            body_chunks: list[bytes] = []

            def on_response(http_stream: Any, status_code: int, headers: list[tuple[str, str]], **kwargs: Any) -> None:
                status_holder[0] = status_code
                response_headers.extend(headers)

            def on_body(http_stream: Any, chunk: bytes, **kwargs: Any) -> None:
                body_chunks.append(chunk)

            stream = connection.request(
                crt_request,
                on_response=on_response,
                on_body=on_body,
            )
            stream.activate()
            stream.completion_future.result()

            fields = Fields()
            for header_name, header_val in response_headers:
                try:
                    fields[header_name].add(header_val)
                except KeyError:
                    fields[header_name] = Field(
                        name=header_name,
                        values=[header_val],
                        kind="header",
                    )

            return AWSCRTHTTPResponse(
                status=status_holder[0],
                fields=fields,
                body=b"".join(body_chunks),
            )
        except AwsCrtError as e:
            if e.name in self._TIMEOUT_ERROR_NAMES:
                raise _CRTTimeoutError(f"CRT {e.name}: {e.message}") from e
            raise

    def _get_connection(self, url: core_interfaces.URI) -> "HttpClientConnection":
        connection_key = (url.scheme, url.host, url.port)
        connection = self._connections.get(connection_key)

        if connection and connection.is_open():
            return connection

        connection = self._create_connection(url)
        self._connections[connection_key] = connection
        return connection

    def _create_connection(self, url: core_interfaces.URI) -> "HttpClientConnection":
        connection = self._build_new_connection(url)
        self._validate_connection(connection)
        return connection

    def _build_new_connection(self, url: core_interfaces.URI) -> "HttpClientConnection":
        if url.scheme == "http":
            port = self._HTTP_PORT
            tls_connection_options = None
        elif url.scheme == "https":
            port = self._HTTPS_PORT
            tls_connection_options = self._tls_ctx.new_connection_options()
            tls_connection_options.set_server_name(url.host)
            tls_connection_options.set_alpn_list(["http/1.1"])
        else:
            raise SmithyHTTPError(
                f"AWSCRTHTTPClient does not support URL scheme {url.scheme}"
            )
        if url.port is not None:
            port = url.port

        connect_future = crt_http.HttpClientConnection.new(
            bootstrap=self._client_bootstrap,
            host_name=url.host,
            port=port,
            socket_options=self._socket_options,
            tls_connection_options=tls_connection_options,
        )
        return connect_future.result()

    def _validate_connection(self, connection: "HttpClientConnection") -> None:
        force_http_2 = self._config.force_http_2
        if force_http_2 and connection.version is not crt_http.HttpVersion.Http2:
            connection.close()
            negotiated = crt_http.HttpVersion(connection.version).name
            raise SmithyHTTPError(f"HTTP/2 could not be negotiated: {negotiated}")

    def _render_path(self, url: core_interfaces.URI) -> str:
        path = url.path if url.path is not None else "/"
        query = f"?{url.query}" if url.query is not None else ""
        return f"{path}{query}"

    def _marshal_request(
        self, request: http_sync_interfaces.HTTPRequest
    ) -> "crt_http.HttpRequest":
        """Create CRT HttpRequest from sync HTTPRequest."""
        headers_list: list[tuple[str, str]] = []
        if "host" not in request.fields:
            request.fields.set_field(
                Field(name="host", values=[request.destination.netloc])
            )

        if "accept" not in request.fields:
            request.fields.set_field(Field(name="accept", values=["*/*"]))

        for fld in request.fields.entries.values():
            if fld.kind != "header":
                continue
            for val in fld.values:
                headers_list.append((fld.name, val))

        path = self._render_path(request.destination)
        headers = crt_http.HttpHeaders(headers_list)

        crt_request = crt_http.HttpRequest(
            method=request.method,
            path=path,
            headers=headers,
        )
        return crt_request

    def _create_body_stream(self, body: StreamingBlob) -> core_interfaces.BytesReader | None:
        """Convert body to a BytesReader for CRT."""
        if core_interfaces.is_bytes_reader(body):
            return body
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body) if isinstance(body, bytearray) else body
            return BytesIO(data) if data else None
        return None

    def __deepcopy__(self, memo: Any) -> "AWSCRTHTTPClient":
        return AWSCRTHTTPClient(
            eventloop=self._eventloop,
            client_config=deepcopy(self._config),
        )
