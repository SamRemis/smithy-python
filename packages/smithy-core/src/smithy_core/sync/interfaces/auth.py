# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
from collections.abc import Mapping
from typing import Any, Protocol

from ...interfaces import TypedProperties as _TypedProperties
from ...interfaces.identity import Identity
from ...shapes import ShapeID
from . import Request
from .identity import IdentityResolver


class Signer[R: Request, I, SP: Mapping[str, Any]](Protocol):
    """A class that signs requests before they are sent."""

    def sign(self, *, request: R, identity: I, properties: SP) -> R:
        """Get a signed version of the request.

        :param request: The request to be signed.
        :param identity: The identity to use to sign the request.
        :param properties: Additional properties used to sign the request.
        """
        ...


class AuthScheme[R: Request, I: Identity, IP: Mapping[str, Any], SP: Mapping[str, Any]](
    Protocol
):
    """A class that coordinates identity and auth."""

    scheme_id: ShapeID
    """The ID of the auth scheme."""

    def identity_properties(self, *, context: _TypedProperties) -> IP:
        """Construct identity properties from the request context.

        :param context: The context of the request.
        """
        ...

    def identity_resolver(
        self, *, context: _TypedProperties
    ) -> IdentityResolver[I, IP]:
        """Get an identity resolver for the request.

        :param context: The context of the request.
        """
        ...

    def signer_properties(self, *, context: _TypedProperties) -> SP:
        """Construct signer properties from the request context.

        :param context: The context of the request.
        """
        ...

    def signer(self) -> Signer[R, I, SP]:
        """Get a signer for the request."""
        ...
