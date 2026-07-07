"""Shared client factories for the sync-benchmark scripts.

All clients point at the local mock server on http://localhost:8888.
"""

ENDPOINT = "http://localhost:8888"


def make_async_client():
    from aws_sdk_polly.client import PollyClient
    from aws_sdk_polly.config import Config
    from smithy_aws_core.identity.static import StaticCredentialsResolver

    config = Config(
        endpoint_uri=ENDPOINT,
        region="us-east-1",
        aws_credentials_identity_resolver=StaticCredentialsResolver(),
        aws_access_key_id="fake",
        aws_secret_access_key="fake",
    )
    return PollyClient(config)


def make_sync_a_client():
    from aws_sdk_polly.sync_client import PollySyncClient
    from aws_sdk_polly.config import Config
    from aws_sdk_polly._private.schemas import PARROT_V1
    from smithy_http.sync.crt import AWSCRTHTTPClient
    from smithy_aws_core.sync.endpoints.standard_regional import (
        StandardRegionalEndpointsResolver,
    )
    from smithy_aws_core.sync.protocols import RestJsonClientProtocol
    from smithy_aws_core.sync.auth.sigv4 import SigV4AuthScheme
    from smithy_aws_core.sync.identity.static import StaticCredentialsResolver
    from smithy_core.shapes import ShapeID

    #We need to use the sync components for some of these, so we can't rely on defaults
    config = Config(
        endpoint_uri=ENDPOINT,
        region="us-east-1",
        endpoint_resolver=StandardRegionalEndpointsResolver(endpoint_prefix="polly"),
        protocol=RestJsonClientProtocol(PARROT_V1),
        transport=AWSCRTHTTPClient(),
        aws_credentials_identity_resolver=StaticCredentialsResolver(),
        aws_access_key_id="fake",
        aws_secret_access_key="fake",
    )
    config.auth_schemes = {ShapeID("aws.auth#sigv4"): SigV4AuthScheme(service="polly")}
    return PollySyncClient(config)


def make_sync_b_client():
    from sync_facade import PollyTypedSyncClient

    return PollyTypedSyncClient(make_async_client())
