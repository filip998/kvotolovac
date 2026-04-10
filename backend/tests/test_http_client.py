from __future__ import annotations

import asyncio
import time

import pytest
import httpx

from app.scrapers.http_client import HttpClient


class MockTransport(httpx.AsyncBaseTransport):
    """Mock transport that returns configurable responses."""

    def __init__(self, responses: list[httpx.Response] | None = None):
        self._responses = list(responses or [])
        self._call_count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._call_count += 1
        if self._responses:
            return self._responses.pop(0)
        return httpx.Response(200, json={"ok": True})

    @property
    def call_count(self) -> int:
        return self._call_count


@pytest.mark.asyncio
async def test_post_json_success():
    client = HttpClient(rate_limit_per_second=0)
    # Override the internal client with a mock transport
    client._client = httpx.AsyncClient(transport=MockTransport())
    result = await client.post_json("https://example.com/api", json_body={"test": 1})
    assert result == {"ok": True}
    await client.close()


@pytest.mark.asyncio
async def test_get_json_success():
    client = HttpClient(rate_limit_per_second=0)
    # Override the internal client with a mock transport
    client._client = httpx.AsyncClient(transport=MockTransport())
    result = await client.get_json("https://example.com/api", params={"key": "value"})
    assert result == {"ok": True}
    await client.close()



@pytest.mark.asyncio
async def test_retry_on_server_error():
    transport = MockTransport(responses=[
        httpx.Response(500, json={"error": "server error"}),
        httpx.Response(500, json={"error": "server error"}),
        httpx.Response(200, json={"ok": True}),
    ])
    client = HttpClient(max_retries=3, backoff_base=0.01, rate_limit_per_second=0)
    client._client = httpx.AsyncClient(transport=transport)

    result = await client.post_json("https://example.com/api")
    assert result == {"ok": True}
    assert transport.call_count == 3
    await client.close()


@pytest.mark.asyncio
async def test_retry_exhausted_raises():
    transport = MockTransport(responses=[
        httpx.Response(500, json={"error": "fail"}),
        httpx.Response(500, json={"error": "fail"}),
        httpx.Response(500, json={"error": "fail"}),
        httpx.Response(500, json={"error": "fail"}),
    ])
    client = HttpClient(max_retries=3, backoff_base=0.01, rate_limit_per_second=0)
    client._client = httpx.AsyncClient(transport=transport)

    with pytest.raises(httpx.HTTPStatusError):
        await client.post_json("https://example.com/api")

    assert transport.call_count == 4  # initial + 3 retries
    await client.close()


@pytest.mark.asyncio
async def test_retry_on_429():
    transport = MockTransport(responses=[
        httpx.Response(429, json={"error": "rate limited"}),
        httpx.Response(200, json={"ok": True}),
    ])
    client = HttpClient(max_retries=3, backoff_base=0.01, rate_limit_per_second=0)
    client._client = httpx.AsyncClient(transport=transport)

    result = await client.post_json("https://example.com/api")
    assert result == {"ok": True}
    assert transport.call_count == 2
    await client.close()


@pytest.mark.asyncio
async def test_timeout_handling():
    class TimeoutTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

    client = HttpClient(max_retries=1, backoff_base=0.01, rate_limit_per_second=0)
    client._client = httpx.AsyncClient(transport=TimeoutTransport())

    with pytest.raises(httpx.ReadTimeout):
        await client.post_json("https://example.com/api")

    await client.close()


@pytest.mark.asyncio
async def test_close():
    client = HttpClient()
    client._client = httpx.AsyncClient()
    assert not client._client.is_closed
    await client.close()
    assert client._client is None


@pytest.mark.asyncio
async def test_get_json_rate_limit_is_concurrency_safe():
    class RecordingTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.started_at: list[float] = []

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.started_at.append(time.monotonic())
            await asyncio.sleep(0.1)
            return httpx.Response(200, json={"ok": True})

    transport = RecordingTransport()
    client = HttpClient(rate_limit_per_second=20)
    client._client = httpx.AsyncClient(transport=transport)

    started = time.monotonic()
    await asyncio.gather(
        client.get_json("https://example.com/api/1"),
        client.get_json("https://example.com/api/2"),
        client.get_json("https://example.com/api/3"),
    )
    elapsed = time.monotonic() - started

    assert len(transport.started_at) == 3
    gaps = [
        transport.started_at[index] - transport.started_at[index - 1]
        for index in range(1, len(transport.started_at))
    ]
    assert all(gap >= 0.045 for gap in gaps)
    assert elapsed < 0.3

    await client.close()
