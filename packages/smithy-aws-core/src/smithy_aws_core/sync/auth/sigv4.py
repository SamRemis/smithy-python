#  Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
from typing import Protocol, Self

from aws_sdk_signers import SigV4Signer as _AWSSigV4Signer
from aws_sdk_signers import SigV4SigningProperties
from smithy_core.exceptions import SmithyIdentityError
from smithy_core.interfaces import TypedProperties as _TypedProperties
from smithy_core.sync.interfaces.auth import AuthScheme, Signer
from smithy_core.sync.interfaces.identity import IdentityResolver
from smithy_core.types import PropertyKey
from smithy_http.sync.interfaces import HTTPRequest

from ...identity import (
    AWS_IDENTITY_CONFIG,
    AWSCredentialsIdentity,
    AWSIdentityProperties,
)
from ...traits import SigV4Trait


class SigV4Config(Protocol):
    region: str | None
    aws_credentials_identity_resolver: (
        IdentityResolver[AWSCredentialsIdentity, AWSIdentityProperties] | None
    )


SIGV4_CONFIG = PropertyKey(key="config", value_type=SigV4Config)

type SigV4Signer = Signer[HTTPRequest, AWSCredentialsIdentity, SigV4SigningProperties]


class SigV4AuthScheme(
    AuthScheme[
        HTTPRequest,
        AWSCredentialsIdentity,
        AWSIdentityProperties,
        SigV4SigningProperties,
    ]
):
    """SigV4 AuthScheme (sync)."""

    scheme_id = SigV4Trait.id
    _signer: SigV4Signer

    def __init__(
        self,
        *,
        service: str,
        signer: SigV4Signer | None = None,
    ) -> None:
        self._signer = signer or _AWSSigV4Signer()  # type: ignore
        self._service = service

    def identity_properties(
        self, *, context: _TypedProperties
    ) -> AWSIdentityProperties:
        config = context[AWS_IDENTITY_CONFIG]
        return {
            "access_key_id": config.aws_access_key_id,
            "secret_access_key": config.aws_secret_access_key,
            "session_token": config.aws_session_token,
        }

    def identity_resolver(
        self, *, context: _TypedProperties
    ) -> IdentityResolver[AWSCredentialsIdentity, AWSIdentityProperties]:
        config = context.get(SIGV4_CONFIG)
        if config is None or config.aws_credentials_identity_resolver is None:
            raise SmithyIdentityError(
                "Attempted to use SigV4 auth, but aws_credentials_identity_resolver was not "
                "set on the config."
            )
        return config.aws_credentials_identity_resolver

    def signer_properties(self, *, context: _TypedProperties) -> SigV4SigningProperties:
        config = context.get(SIGV4_CONFIG)
        if config is None or config.region is None:
            raise SmithyIdentityError(
                "Attempted to use SigV4 auth, but region was not set on the config."
            )
        return {
            "region": config.region,
            "service": self._service,
        }

    def signer(self) -> SigV4Signer:
        return self._signer

    @classmethod
    def from_trait(cls, trait: SigV4Trait, /) -> Self:
        return cls(service=trait.name)
