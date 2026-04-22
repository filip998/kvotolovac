from __future__ import annotations

import asyncio
import time

import app.scrapers.http_client as http_client_module
import httpx
import pytest

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
    client._client = httpx.AsyncClient(transport=MockTransport())
    result = await client.post_json("https://example.com/api", json_body={"test": 1})
    assert result == {"ok": True}
    await client.close()


@pytest.mark.asyncio
async def test_get_json_success():
    client = HttpClient(rate_limit_per_second=0)
    client._client = httpx.AsyncClient(transport=MockTransport())
    result = await client.get_json("https://example.com/api", params={"key": "value"})
    assert result == {"ok": True}
    await client.close()


@pytest.mark.asyncio
async def test_retry_on_server_error():
    transport = MockTransport(
        responses=[
            httpx.Response(500, json={"error": "server error"}),
            httpx.Response(500, json={"error": "server error"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    client = HttpClient(max_retries=3, backoff_base=0.01, rate_limit_per_second=0)
    client._client = httpx.AsyncClient(transport=transport)

    result = await client.post_json("https://example.com/api")
    assert result == {"ok": True}
    assert transport.call_count == 3
    await client.close()


@pytest.mark.asyncio
async def test_retry_exhausted_raises():
    transport = MockTransport(
        responses=[
            httpx.Response(500, json={"error": "fail"}),
            httpx.Response(500, json={"error": "fail"}),
            httpx.Response(500, json={"error": "fail"}),
            httpx.Response(500, json={"error": "fail"}),
        ]
    )
    client = HttpClient(max_retries=3, backoff_base=0.01, rate_limit_per_second=0)
    client._client = httpx.AsyncClient(transport=transport)

    with pytest.raises(httpx.HTTPStatusError):
        await client.post_json("https://example.com/api")

    assert transport.call_count == 4
    await client.close()


@pytest.mark.asyncio
async def test_retry_on_429():
    transport = MockTransport(
        responses=[
            httpx.Response(429, json={"error": "rate limited"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
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


@pytest.mark.asyncio
async def test_rate_limit_is_isolated_per_http_client():
    class RecordingTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.started_at: list[float] = []

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.started_at.append(time.monotonic())
            await asyncio.sleep(0.01)
            return httpx.Response(200, json={"ok": True})

    transport_a = RecordingTransport()
    transport_b = RecordingTransport()
    client_a = HttpClient(rate_limit_per_second=5)
    client_b = HttpClient(rate_limit_per_second=5)
    client_a._client = httpx.AsyncClient(transport=transport_a)
    client_b._client = httpx.AsyncClient(transport=transport_b)

    await asyncio.gather(
        client_a.get_json("https://example.com/api/a"),
        client_b.get_json("https://example.com/api/b"),
    )

    assert len(transport_a.started_at) == 1
    assert len(transport_b.started_at) == 1
    assert abs(transport_a.started_at[0] - transport_b.started_at[0]) < 0.08

    await client_a.close()
    await client_b.close()


class _FakeResponse:
    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamContext:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        return False


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.is_closed = False

    def stream(self, method: str, url: str, *, params=None, headers=None, timeout=None):
        del method, url, params, headers, timeout
        return _FakeStreamContext(self.response)


class _TrackingStreamContext(_FakeStreamContext):
    def __init__(self, response: _FakeResponse) -> None:
        super().__init__(response)
        self.exited = False

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self.exited = True
        return await super().__aexit__(exc_type, exc, tb)


class _SequencedFakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.contexts = [_TrackingStreamContext(response) for response in responses]
        self.is_closed = False

    def stream(self, method: str, url: str, *, params=None, headers=None, timeout=None):
        del method, url, params, headers, timeout
        return self.contexts.pop(0)


@pytest.mark.asyncio
async def test_get_sse_json_reads_first_json_message():
    client = HttpClient(max_retries=0)
    client._client = _FakeClient(_FakeResponse([": ping", 'data: [{"event_id": 1}]', ""]))  # type: ignore[assignment]

    result = await client.get_sse_json("https://example.test/stream")

    assert result == [[{"event_id": 1}]]


@pytest.mark.asyncio
async def test_get_sse_json_raises_when_stream_has_no_data():
    client = HttpClient(max_retries=0)
    client._client = _FakeClient(_FakeResponse([": keepalive", ""]))  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="No SSE data received"):
        await client.get_sse_json("https://example.test/stream")


@pytest.mark.asyncio
async def test_get_sse_json_releases_stream_before_retry_backoff(monkeypatch):
    retry_response = _FakeResponse([], status_code=429)
    success_response = _FakeResponse(['data: [{"event_id": 2}]', ""])
    fake_client = _SequencedFakeClient([retry_response, success_response])

    client = HttpClient(max_retries=1, backoff_base=0.01, rate_limit_per_second=0)
    client._client = fake_client  # type: ignore[assignment]
    first_context = fake_client.contexts[0]
    rotated: list[bool] = []

    def fake_rotate_proxy() -> None:
        assert first_context.exited is True
        rotated.append(True)

    async def fake_sleep(delay: float) -> None:
        assert delay == 0.01
        assert first_context.exited is True

    monkeypatch.setattr(client, "_rotate_proxy", fake_rotate_proxy)
    monkeypatch.setattr(http_client_module.asyncio, "sleep", fake_sleep)

    result = await client.get_sse_json("https://example.test/stream")

    assert result == [[{"event_id": 2}]]
    assert rotated == [True]
