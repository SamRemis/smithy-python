# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import os
from collections.abc import Iterable
from typing import Any, Final

from smithy_core import URI as _URI
from smithy_core.codecs import Codec
from smithy_core.deserializers import DeserializeableShape
from smithy_core.documents import TypeRegistry
from smithy_core.exceptions import (
    CallError,
    DiscriminatorError,
    ExpectationNotMetError,
    MissingDependencyError,
    ModeledError,
)
from smithy_core.interfaces import (
    Endpoint,
    SeekableBytesReader,
    StreamingBlob as SyncStreamingBlob,
    TypedProperties,
    URI,
    is_streaming_blob,
)
from smithy_core.prelude import DOCUMENT
from smithy_core.schemas import APIOperation, Schema
from smithy_core.serializers import SerializeableShape
from smithy_core.shapes import ShapeID, ShapeType
from smithy_core.sync.interfaces import ClientProtocol
from smithy_core.traits import EndpointTrait, HTTPTrait
from smithy_core.types import TimestampFormat
from smithy_http.deserializers import HTTPResponseDeserializer
from smithy_http.serializers import HTTPRequestSerializer
from smithy_http.sync.interfaces import HTTPErrorIdentifier, HTTPRequest, HTTPResponse

from .._private.query.errors import create_aws_query_error  # noqa: F401  (parity)
from ..traits import RestJson1Trait
from ..utils import parse_document_discriminator, parse_error_code

try:
    from smithy_json import JSONCodec, JSONDocument

    _HAS_JSON = True
except ImportError:
    _HAS_JSON = False  # type: ignore


def _assert_json() -> None:
    if not _HAS_JSON:
        raise MissingDependencyError(
            "Attempted to use JSON codec, but smithy-json is not installed."
        )


def _to_sync_body(body: Any) -> Any:
    if isinstance(body, (bytes, bytearray)):
        return body
    inner = getattr(body, "_data", None)
    if inner is not None:
        return inner
    return body


class HttpClientProtocol(ClientProtocol[HTTPRequest, HTTPResponse]):
    """Sync HTTP-based protocol base."""

    def set_service_endpoint(
        self,
        *,
        request: HTTPRequest,
        endpoint: Endpoint,
    ) -> HTTPRequest:
        uri = endpoint.uri
        previous = request.destination

        path = previous.path or uri.path
        if uri.path is not None and previous.path is not None:
            path = os.path.join(uri.path, previous.path.lstrip("/"))

        if path is not None and not path.startswith("/"):
            path = "/" + path

        query = previous.query or uri.query
        if uri.query and previous.query:
            query = f"{uri.query}&{previous.query}"

        request.destination = _URI(
            scheme=uri.scheme,
            username=uri.username or previous.username,
            password=uri.password or previous.password,
            host=uri.host,
            port=uri.port or previous.port,
            path=path,
            query=query,
            fragment=uri.fragment or previous.fragment,
        )

        return request


class HttpBindingClientProtocol(HttpClientProtocol):
    """Sync HTTP-binding-based protocol base."""

    @property
    def payload_codec(self) -> Codec:
        raise NotImplementedError()

    @property
    def content_type(self) -> str:
        raise NotImplementedError()

    @property
    def error_identifier(self) -> HTTPErrorIdentifier:
        raise NotImplementedError()

    def serialize_request[
        OperationInput: "SerializeableShape",
        OperationOutput: "DeserializeableShape",
    ](
        self,
        *,
        operation: APIOperation[OperationInput, OperationOutput],
        input: OperationInput,
        endpoint: URI,
        context: TypedProperties,
    ) -> HTTPRequest:
        serializer = HTTPRequestSerializer(
            payload_codec=self.payload_codec,
            http_trait=operation.schema.expect_trait(HTTPTrait),
            endpoint_trait=operation.schema.get_trait(EndpointTrait),
        )

        input.serialize(serializer=serializer)
        request = serializer.result

        if request is None:
            raise ExpectationNotMetError(
                "Expected request to be serialized, but was None"
            )

        request.body = _to_sync_body(request.body)
        return request  # type: ignore[return-value]

    def deserialize_response[
        OperationInput: "SerializeableShape",
        OperationOutput: "DeserializeableShape",
    ](
        self,
        *,
        operation: APIOperation[OperationInput, OperationOutput],
        request: HTTPRequest,
        response: HTTPResponse,
        error_registry: TypeRegistry,
        context: TypedProperties,
    ) -> OperationOutput:
        if not self._is_success(operation, context, response):
            raise self._create_error(
                operation=operation,
                request=request,
                response=response,
                response_body=self._buffer_body(response.body),
                error_registry=error_registry,
                context=context,
            )

        body: SyncStreamingBlob | None = None
        if not operation.output_stream_member and not is_streaming_blob(response.body):
            body = self._buffer_body(response.body)
        elif is_streaming_blob(response.body):
            body = None  # deserializer will read from response.body directly
        else:
            body = self._buffer_body(response.body)

        deserializer = HTTPResponseDeserializer(
            payload_codec=self.payload_codec,
            http_trait=operation.schema.expect_trait(HTTPTrait),
            response=response,  # type: ignore[arg-type]
            body=body,
        )

        return operation.output.deserialize(deserializer)

    def _buffer_body(self, stream: Any) -> SyncStreamingBlob:
        if isinstance(stream, (bytes, bytearray)):
            return stream
        if hasattr(stream, "read") and callable(stream.read):
            data = stream.read()
            return data
        if isinstance(stream, Iterable):
            return b"".join(stream)
        return stream

    def _is_success(
        self,
        operation: APIOperation[Any, Any],
        context: TypedProperties,
        response: HTTPResponse,
    ) -> bool:
        return 200 <= response.status < 300

    def _create_error(
        self,
        operation: APIOperation[Any, Any],
        request: HTTPRequest,
        response: HTTPResponse,
        response_body: SyncStreamingBlob,
        error_registry: TypeRegistry,
        context: TypedProperties,
    ) -> CallError:
        error_id = self.error_identifier.identify(
            operation=operation, response=response
        )

        if error_id is None and self._matches_content_type(response):
            if isinstance(response_body, bytearray):
                response_body = bytes(response_body)
            deserializer = self.payload_codec.create_deserializer(source=response_body)
            document = deserializer.read_document(schema=DOCUMENT)

            if document.discriminator in error_registry:
                error_id = document.discriminator
                if isinstance(response_body, SeekableBytesReader):
                    response_body.seek(0)

        if error_id is not None and error_id in error_registry:
            error_shape = error_registry.get(error_id)

            if not issubclass(error_shape, ModeledError):
                raise ExpectationNotMetError(
                    f"Modeled errors must be derived from 'ModeledError', "
                    f"but got {error_shape}"
                )

            deserializer = HTTPResponseDeserializer(
                payload_codec=self.payload_codec,
                http_trait=operation.schema.expect_trait(HTTPTrait),
                response=response,  # type: ignore[arg-type]
                body=response_body,
            )
            return error_shape.deserialize(deserializer)

        message = (
            f"Unknown error for operation {operation.schema.id} "
            f"- status: {response.status}"
        )
        if error_id is not None:
            message += f" - id: {error_id}"
        if response.reason is not None:
            message += f" - reason: {response.status}"

        is_timeout = response.status == 408
        is_throttle = response.status == 429
        fault = "client" if response.status < 500 else "server"

        return CallError(
            message=message,
            fault=fault,
            is_throttling_error=is_throttle,
            is_timeout_error=is_timeout,
            is_retry_safe=is_throttle or is_timeout or None,
        )

    def _matches_content_type(self, response: HTTPResponse) -> bool:
        if "content-type" not in response.fields:
            return False
        return response.fields["content-type"].as_string() == self.content_type


class AWSErrorIdentifier(HTTPErrorIdentifier):
    _HEADER_KEY: Final = "x-amzn-errortype"

    def identify(
        self,
        *,
        operation: APIOperation[Any, Any],
        response: HTTPResponse,
    ) -> ShapeID | None:
        if self._HEADER_KEY not in response.fields:
            return None

        error_field = response.fields[self._HEADER_KEY]
        code = error_field.values[0] if len(error_field.values) > 0 else None
        if code is not None:
            return parse_error_code(code, operation.schema.id.namespace)
        return None


if _HAS_JSON:

    class AWSJSONDocument(JSONDocument):
        @property
        def discriminator(self) -> ShapeID:
            if self.shape_type is ShapeType.STRUCTURE:
                return self._schema.id
            parsed = parse_document_discriminator(
                self, self._settings.default_namespace
            )
            if parsed is None:
                raise DiscriminatorError(
                    f"Unable to parse discriminator for {self.shape_type} document."
                )
            return parsed


class RestJsonClientProtocol(HttpBindingClientProtocol):
    """Sync implementation of the aws.protocols#restJson1 protocol."""

    _id: Final = RestJson1Trait.id
    _contentType: Final = "application/json"
    _error_identifier: Final = AWSErrorIdentifier()

    def __init__(self, service_schema: Schema) -> None:
        _assert_json()
        self._codec: Final = JSONCodec(
            document_class=AWSJSONDocument,
            default_namespace=service_schema.id.namespace,
            default_timestamp_format=TimestampFormat.EPOCH_SECONDS,
        )

    @property
    def id(self) -> ShapeID:
        return self._id

    @property
    def payload_codec(self) -> Codec:
        return self._codec

    @property
    def content_type(self) -> str:
        return self._contentType

    @property
    def error_identifier(self) -> HTTPErrorIdentifier:
        return self._error_identifier
