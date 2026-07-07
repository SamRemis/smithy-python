#  Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import logging
import time
from collections.abc import Callable, Sequence
from copy import copy
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from .. import URI
from ..auth import AuthParams
from ..deserializers import DeserializeableShape, ShapeDeserializer
from ..endpoints import EndpointResolverParams
from ..exceptions import ClientTimeoutError, RetryError, SmithyError
from ..interceptors import (
    InputContext,
    Interceptor,
    OutputContext,
    RequestContext,
    ResponseContext,
)
from ..interfaces import Endpoint, TypedProperties
from ..interfaces.auth import AuthOption, AuthSchemeResolver
from ..schemas import APIOperation
from ..serializers import SerializeableShape
from ..shapes import ShapeID
from ..types import PropertyKey
from .interfaces import (
    ClientProtocol,
    ClientTransport,
    EndpointResolver,
    Request,
    Response,
)
from .interfaces.auth import AuthScheme
from .interfaces.retries import RetryStrategy

if TYPE_CHECKING:
    from typing_extensions import TypeForm

AUTH_SCHEME = PropertyKey(key="auth_scheme", value_type=AuthScheme[Any, Any, Any, Any])

_UNRESOLVED = URI(host="", path="/")
_LOGGER = logging.getLogger(__name__)


def _seek(stream: Any, offset: int, whence: int = 0) -> int | None:
    if (seekable := getattr(stream, "seekable", None)) is not None and not seekable():
        return None
    if (seek_fn := getattr(stream, "seek", None)) is not None:
        return seek_fn(offset, whence)
    return None


@dataclass(kw_only=True, frozen=True)
class ClientCall[I: SerializeableShape, O: DeserializeableShape]:
    """A data class containing all the initial information about an operation
    invocation."""

    input: I
    """The input of the operation."""

    operation: APIOperation[I, O] = field(repr=False)
    """The schema of the operation."""

    context: TypedProperties
    """The initial context of the operation."""

    interceptor: Interceptor[I, O, Any, Any]
    """The interceptor to use in the course of the operation invocation.

    This SHOULD be an InterceptorChain.
    """

    auth_scheme_resolver: AuthSchemeResolver
    """The auth scheme resolver for the operation."""

    supported_auth_schemes: dict[ShapeID, AuthScheme[Any, Any, Any, Any]]
    """The supported auth schemes for the operation."""

    endpoint_resolver: EndpointResolver
    """The endpoint resolver for the operation."""

    retry_strategy: RetryStrategy
    """The retry strategy to use for the operation."""

    retry_scope: str | None = None
    """The retry scope for the operation."""

    def retryable(self) -> bool:
        return self.operation.input_stream_member is None


class RequestPipeline[TRequest: Request, TResponse: Response]:
    """Invokes client operations synchronously."""

    protocol: ClientProtocol[TRequest, TResponse]
    """The protocol to use to serialize the request and deserialize the response."""

    transport: ClientTransport[TRequest, TResponse]
    """The transport to use to send the request and receive the response."""

    def __init__(
        self,
        protocol: ClientProtocol[TRequest, TResponse],
        transport: ClientTransport[TRequest, TResponse],
    ) -> None:
        self.protocol = protocol
        self.transport = transport

    def __call__[I: SerializeableShape, O: DeserializeableShape](
        self, call: ClientCall[I, O], /
    ) -> O:
        """Invoke an operation synchronously.

        :param call: The operation to invoke and associated context.
        """
        output, _ = self._execute_request(call)
        return output

    def output_stream[
        I: SerializeableShape,
        O: DeserializeableShape,
        E: DeserializeableShape,
    ](
        self,
        call: ClientCall[I, O],
        event_type: "TypeForm[E]",
        event_deserializer: Callable[[ShapeDeserializer], E],
        /,
    ) -> tuple[O, Any]:
        """Invoke a server-streaming operation synchronously.

        :param call: The operation to invoke and associated context.
        :param event_type: The event type to receive in the output stream.
        :param event_deserializer: The method used to deserialize events.
        :returns: A tuple of (output, event_receiver).
        """
        output, output_context = self._execute_request(call)
        output_stream = self.protocol.create_event_receiver(
            operation=call.operation,
            request=output_context.transport_request,
            response=output_context.transport_response,
            event_type=event_type,
            event_deserializer=event_deserializer,
            context=output_context.properties,
        )
        return output, output_stream

    def _execute_request[I: SerializeableShape, O: DeserializeableShape](
        self,
        call: ClientCall[I, O],
    ) -> tuple[O, OutputContext[I, O, TRequest, TResponse]]:
        _LOGGER.debug(
            'Making request for operation "%s" with parameters: %s',
            call.operation.schema.id.name,
            call.input,
        )
        output_context = self._handle_execution(call)
        output_context = self._finalize_execution(call, output_context)

        if isinstance(output_context.response, Exception):
            e = output_context.response
            if not isinstance(e, SmithyError):
                raise SmithyError(e) from e
            raise e

        return output_context.response, output_context  # type: ignore

    def _handle_execution[I: SerializeableShape, O: DeserializeableShape](
        self,
        call: ClientCall[I, O],
    ) -> OutputContext[I, O, TRequest | None, TResponse | None]:
        try:
            interceptor = call.interceptor

            input_context = InputContext(request=call.input, properties=call.context)
            interceptor.read_before_execution(input_context)

            input_context = replace(
                input_context,
                request=interceptor.modify_before_serialization(input_context),
            )

            interceptor.read_before_serialization(input_context)
            _LOGGER.debug("Serializing request for: %s", input_context.request)

            transport_request = self.protocol.serialize_request(
                operation=call.operation,
                input=call.input,
                endpoint=_UNRESOLVED,
                context=input_context.properties,
            )
            request_context = RequestContext(
                request=input_context.request,
                transport_request=transport_request,
                properties=input_context.properties,
            )

            _LOGGER.debug(
                "Serialization complete. Transport request: %s", transport_request
            )
        except Exception as e:
            return OutputContext(
                request=call.input,
                response=e,
                transport_request=None,
                transport_response=None,
                properties=call.context,
            )

        try:
            interceptor.read_after_serialization(request_context)
            request_context = replace(
                request_context,
                transport_request=interceptor.modify_before_retry_loop(request_context),
            )

            return self._retry(call, request_context)
        except Exception as e:
            return OutputContext(
                request=request_context.request,
                response=e,
                transport_request=request_context.transport_request,
                transport_response=None,
                properties=request_context.properties,
            )

    def _retry[I: SerializeableShape, O: DeserializeableShape](
        self,
        call: ClientCall[I, O],
        request_context: RequestContext[I, TRequest],
    ) -> OutputContext[I, O, TRequest | None, TResponse | None]:
        if not call.retryable():
            return self._handle_attempt(call, request_context)

        retry_strategy = call.retry_strategy
        retry_token = retry_strategy.acquire_initial_retry_token(
            token_scope=call.retry_scope
        )

        while True:
            if retry_token.retry_delay:
                time.sleep(retry_token.retry_delay)

            output_context = self._handle_attempt(
                call,
                replace(
                    request_context,
                    transport_request=copy(request_context.transport_request),
                ),
            )

            if isinstance(output_context.response, Exception):
                try:
                    retry_token = retry_strategy.refresh_retry_token_for_retry(
                        token_to_renew=retry_token,
                        error=output_context.response,
                    )
                except RetryError:
                    raise output_context.response

                _LOGGER.debug(
                    "Retry needed. Attempting request #%s in %.4f seconds.",
                    retry_token.retry_count + 1,
                    retry_token.retry_delay,
                )

                _seek(request_context.transport_request.body, 0)
            else:
                retry_strategy.record_success(token=retry_token)
                return output_context

    def _handle_attempt[I: SerializeableShape, O: DeserializeableShape](
        self,
        call: ClientCall[I, O],
        request_context: RequestContext[I, TRequest],
    ) -> OutputContext[I, O, TRequest, TResponse | None]:
        output_context: OutputContext[I, O, TRequest, TResponse | None]
        try:
            interceptor = call.interceptor
            interceptor.read_before_attempt(request_context)

            endpoint_params = EndpointResolverParams(
                operation=call.operation,
                input=call.input,
                context=request_context.properties,
            )
            _LOGGER.debug("Calling endpoint resolver with params: %s", endpoint_params)
            endpoint: Endpoint = call.endpoint_resolver.resolve_endpoint(
                endpoint_params
            )
            _LOGGER.debug("Endpoint resolver result: %s", endpoint)

            request_context = replace(
                request_context,
                transport_request=self.protocol.set_service_endpoint(
                    request=request_context.transport_request, endpoint=endpoint
                ),
            )

            request_context = replace(
                request_context,
                transport_request=interceptor.modify_before_signing(request_context),
            )
            interceptor.read_before_signing(request_context)

            auth_params = AuthParams[I, O](
                protocol_id=self.protocol.id,
                operation=call.operation,
                context=request_context.properties,
            )
            auth = self._resolve_auth(call, auth_params)
            if auth is not None:
                option, scheme = auth
                request_context.properties[AUTH_SCHEME] = scheme
                identity_resolver = scheme.identity_resolver(context=call.context)

                identity_properties = scheme.identity_properties(
                    context=request_context.properties
                )
                identity_properties.update(option.identity_properties)

                identity = identity_resolver.get_identity(
                    properties=identity_properties
                )

                signer_properties = scheme.signer_properties(
                    context=request_context.properties
                )
                signer_properties.update(option.identity_properties)
                _LOGGER.debug("Request to sign: %s", request_context.transport_request)
                _LOGGER.debug("Signer properties: %s", signer_properties)

                signer = scheme.signer()
                request_context = replace(
                    request_context,
                    transport_request=signer.sign(
                        request=request_context.transport_request,
                        identity=identity,
                        properties=signer_properties,
                    ),
                )

            interceptor.read_after_signing(request_context)
            request_context = replace(
                request_context,
                transport_request=interceptor.modify_before_transmit(request_context),
            )
            interceptor.read_before_transmit(request_context)

            _LOGGER.debug("Sending request %s", request_context.transport_request)

            try:
                transport_response = self.transport.send(
                    request=request_context.transport_request
                )
            except Exception as e:
                if isinstance(e, self.transport.TIMEOUT_EXCEPTIONS):
                    raise ClientTimeoutError(message="A timeout error occurred.") from e
                raise

            _LOGGER.debug("Received response: %s", transport_response)

            response_context = ResponseContext(
                request=request_context.request,
                transport_request=request_context.transport_request,
                transport_response=transport_response,
                properties=request_context.properties,
            )

            interceptor.read_after_transmit(response_context)

            response_context = replace(
                response_context,
                transport_response=interceptor.modify_before_deserialization(
                    response_context
                ),
            )

            interceptor.read_before_deserialization(response_context)

            _LOGGER.debug(
                "Deserializing response: %s", response_context.transport_response
            )

            output = self.protocol.deserialize_response(
                operation=call.operation,
                request=response_context.transport_request,
                response=response_context.transport_response,
                error_registry=call.operation.error_registry,
                context=response_context.properties,
            )

            _LOGGER.debug("Deserialization complete. Output: %s", output)

            output_context = OutputContext(
                request=response_context.request,
                response=output,
                transport_request=response_context.transport_request,
                transport_response=response_context.transport_response,
                properties=response_context.properties,
            )

            interceptor.read_after_deserialization(output_context)
        except Exception as e:
            output_context = OutputContext(
                request=request_context.request,
                response=e,
                transport_request=request_context.transport_request,
                transport_response=None,
                properties=request_context.properties,
            )

        return self._finalize_attempt(call, output_context)

    def _resolve_auth[I: SerializeableShape, O: DeserializeableShape](
        self, call: ClientCall[Any, Any], params: AuthParams[I, O]
    ) -> tuple[AuthOption, AuthScheme[TRequest, Any, Any, Any]] | None:
        auth_options: Sequence[AuthOption] = (
            call.auth_scheme_resolver.resolve_auth_scheme(auth_parameters=params)
        )

        for option in auth_options:
            if (
                scheme := call.supported_auth_schemes.get(option.scheme_id)
            ) is not None:
                return option, scheme

        return None

    def _finalize_attempt[I: SerializeableShape, O: DeserializeableShape](
        self,
        call: ClientCall[I, O],
        output_context: OutputContext[I, O, TRequest, TResponse | None],
    ) -> OutputContext[I, O, TRequest, TResponse | None]:
        interceptor = call.interceptor
        try:
            output_context = replace(
                output_context,
                response=interceptor.modify_before_attempt_completion(output_context),
            )
        except Exception as e:
            output_context = replace(output_context, response=e)

        try:
            interceptor.read_after_attempt(output_context)
        except Exception as e:
            output_context = replace(output_context, response=e)

        return output_context

    def _finalize_execution[I: SerializeableShape, O: DeserializeableShape](
        self,
        call: ClientCall[I, O],
        output_context: OutputContext[I, O, TRequest | None, TResponse | None],
    ) -> OutputContext[I, O, TRequest | None, TResponse | None]:
        interceptor = call.interceptor
        try:
            output_context = replace(
                output_context,
                response=interceptor.modify_before_completion(output_context),
            )
        except Exception as e:
            output_context = replace(output_context, response=e)

        try:
            interceptor.read_after_execution(output_context)
        except Exception as e:
            output_context = replace(output_context, response=e)

        return output_context
