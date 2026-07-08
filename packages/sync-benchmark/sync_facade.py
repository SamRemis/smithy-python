"""Option B: typed sync wrapper over an async client via a background event loop.

One shared daemon thread runs a persistent asyncio loop. `PollyTypedSyncClient`
exposes real, statically-typed sync methods per operation that dispatch the
underlying coroutine onto that loop and block on the result. This is the shape
codegen would emit for a sync client if the sync path is B under the hood.
"""

import asyncio
import threading
from typing import Any

from aws_sdk_polly.config import Plugin
from aws_sdk_polly.models import DescribeVoicesInput, DescribeVoicesOutput


def _start_background_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()

    def runner():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    t = threading.Thread(target=runner, daemon=True, name="sync-facade-loop")
    t.start()
    return loop


_LOOP = _start_background_loop()


def _run_sync(coro):
    return asyncio.run_coroutine_threadsafe(coro, _LOOP).result()


class PollyTypedSyncClient:

    def __init__(self, async_client: Any) -> None:
        self._client = async_client
    def describe_voices(
        self, input: DescribeVoicesInput, plugins: list[Plugin] | None = None
    ) -> DescribeVoicesOutput:
        return _run_sync(self._client.describe_voices(input, plugins=plugins))
