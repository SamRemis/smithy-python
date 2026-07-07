#  Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
from typing import Any, Protocol

from smithy_core.interfaces import StreamingBlob
from smithy_core.schemas import APIOperation
from smithy_core.shapes import ShapeID
from smithy_core.sync.interfaces import ClientTransport, Request, Response

from ...interfaces import (
    Fields,
    HTTPClientConfiguration,
    HTTPRequestConfiguration,
)


class HTTPRequest(Request, Protocol):
    """HTTP primitive for a sync HTTP request.

    :param destination: The URI where the request should be sent to.
    :param method: The HTTP method of the request, for example "GET".
    :param fields: ``Fields`` object containing HTTP headers and trailers.
    :param body: A streamable collection of bytes.
    """

    method: str
    fields: Fields

    def consume_body(self) -> bytes:
        """Read request body and return as bytes."""
        body = self.body
        if isinstance(body, bytes):
            return body
        if isinstance(body, bytearray):
            return bytes(body)
        return body.read()


class HTTPResponse(Response, Protocol):
    """HTTP primitives returned from an Exchange, used to construct a client
    response."""

    @property
    def status(self) -> int:
        """The 3 digit response status code (1xx, 2xx, 3xx, 4xx, 5xx)."""
        ...

    @property
    def fields(self) -> Fields:
        """``Fields`` object containing HTTP headers and trailers."""
        ...

    @property
    def reason(self) -> str | None:
        """Optional string provided by the server explaining the status."""
        ...

    def consume_body(self) -> bytes:
        """Read response body and return as bytes."""
        body = self.body
        if isinstance(body, bytes):
            return body
        if isinstance(body, bytearray):
            return bytes(body)
        return body.read()


class HTTPClient(ClientTransport[HTTPRequest, HTTPResponse], Protocol):
    """A synchronous HTTP client interface."""

    def __init__(self, *, client_config: HTTPClientConfiguration | None) -> None:
        """
        :param client_config: Configuration that applies to all requests made with this
        client.
        """
        ...

    def send(
        self,
        request: HTTPRequest,
        *,
        request_config: HTTPRequestConfiguration | None = None,
    ) -> HTTPResponse:
        """Send HTTP request over the wire and return the response.

        :param request: The request including destination URI, fields, payload.
        :param request_config: Configuration specific to this request.
        """
        ...


class HTTPErrorIdentifier:
    """A class that uses HTTP response metadata to identify errors."""

    def identify(
        self,
        *,
        operation: APIOperation[Any, Any],
        response: HTTPResponse,
    ) -> ShapeID | None:
        """Identify the ShapeID of an error from an HTTP response."""
