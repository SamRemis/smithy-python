# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
from dataclasses import dataclass, field

from smithy_core import interfaces as core_interfaces
from smithy_core.interfaces import StreamingBlob

from .. import interfaces as http_interfaces
from . import interfaces as http_sync_interfaces


@dataclass(kw_only=True)
class HTTPRequest(http_sync_interfaces.HTTPRequest):
    """HTTP primitives for a sync HTTP request."""

    destination: core_interfaces.URI
    body: StreamingBlob = field(repr=False, default=b"")
    method: str
    fields: http_interfaces.Fields


@dataclass(kw_only=True)
class HTTPResponse:
    """Basic sync HTTP response implementation."""

    body: StreamingBlob = field(repr=False, default=b"")
    """The response payload."""

    status: int
    """The 3 digit response status code."""

    fields: http_interfaces.Fields
    """HTTP header and trailer fields."""

    reason: str | None = None
    """Optional string provided by the server explaining the status."""

    def consume_body(self) -> bytes:
        """Read the response body and return as bytes."""
        if isinstance(self.body, bytes):
            return self.body
        if isinstance(self.body, bytearray):
            return bytes(self.body)
        # BytesReader
        return self.body.read()
