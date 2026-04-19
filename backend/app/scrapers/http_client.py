from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_BASE = 1.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class HttpClient:
    """Async HTTP client with retry, rate limiting, and optional proxy rotation."""

    def __init__(
        self,
        *,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_base: float = _DEFAULT_BACKOFF_BASE,
        rate_limit_per_second: float = 1.0,
        timeout: float = _DEFAULT_TIMEOUT,
        proxies: list[str] | None = None,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._min_interval = 1.0 / rate_limit_per_second if rate_limit_per_second > 0 else 0
        self._timeout = timeout
        self._proxies = proxies or []
        self._proxy_index = 0
        self._default_headers = default_headers or {}
        self._last_request_time: float = 0.0
        self._rate_limit_lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None

    @property
    def rate_limit_per_second(self) -> float:
        if self._min_interval <= 0:
            return 0.0
        return 1.0 / self._min_interval

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            proxy = self._proxies[self._proxy_index] if self._proxies else None
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                proxy=proxy,
                headers=self._default_headers,
            )
        return self._client

    def _rotate_proxy(self) -> None:
        if self._proxies:
            self._proxy_index = (self._proxy_index + 1) % len(self._proxies)
            # Force new client on next request so it picks up the new proxy
            if self._client and not self._client.is_closed:
                asyncio.get_event_loop().create_task(self._client.aclose())
            self._client = None

    async def _acquire_request_slot(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._rate_limit_lock:
            elapsed = time.monotonic() - self._last_request_time
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_time = time.monotonic()

    async def post_json(
        self,
        url: str,
        *,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        form_data: str | None = None,
    ) -> dict:
        """POST JSON with retry, rate limiting, and proxy rotation.

        If ``form_data`` is provided it is sent as
        ``application/x-www-form-urlencoded`` body (``json_body`` is ignored).
        """
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                client = await self._get_client()
                merged_headers = {**self._default_headers, **(headers or {})}
                await self._acquire_request_slot()

                if form_data is not None:
                    response = await client.post(
                        url,
                        content=form_data.encode(),
                        headers=merged_headers,
                    )
                else:
                    response = await client.post(
                        url,
                        json=json_body,
                        headers=merged_headers,
                    )

                if response.status_code in _RETRYABLE_STATUS_CODES:
                    logger.warning(
                        "Retryable status %d from %s (attempt %d/%d)",
                        response.status_code, url, attempt + 1, self._max_retries + 1,
                    )
                    if attempt < self._max_retries:
                        self._rotate_proxy()
                        await asyncio.sleep(self._backoff_base * (2 ** attempt))
                        continue
                    response.raise_for_status()

                response.raise_for_status()
                return response.json()

            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
                last_error = exc
                logger.warning(
                    "Network error from %s: %s (attempt %d/%d)",
                    url, exc, attempt + 1, self._max_retries + 1,
                )
                if attempt < self._max_retries:
                    self._rotate_proxy()
                    await asyncio.sleep(self._backoff_base * (2 ** attempt))
                    continue

        raise last_error or RuntimeError(f"Failed after {self._max_retries + 1} attempts")

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict:
        """GET JSON with retry, rate limiting, and proxy rotation."""
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                client = await self._get_client()
                merged_headers = {**self._default_headers, **(headers or {})}
                await self._acquire_request_slot()

                response = await client.get(
                    url,
                    params=params,
                    headers=merged_headers,
                )

                if response.status_code in _RETRYABLE_STATUS_CODES:
                    logger.warning(
                        "Retryable status %d from %s (attempt %d/%d)",
                        response.status_code, url, attempt + 1, self._max_retries + 1,
                    )
                    if attempt < self._max_retries:
                        self._rotate_proxy()
                        await asyncio.sleep(self._backoff_base * (2 ** attempt))
                        continue
                    response.raise_for_status()

                response.raise_for_status()
                return response.json()

            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
                last_error = exc
                logger.warning(
                    "Network error from %s: %s (attempt %d/%d)",
                    url, exc, attempt + 1, self._max_retries + 1,
                )
                if attempt < self._max_retries:
                    self._rotate_proxy()
                    await asyncio.sleep(self._backoff_base * (2 ** attempt))
                    continue

        raise last_error or RuntimeError(f"Failed after {self._max_retries + 1} attempts")

    async def put_json(
        self,
        url: str,
        *,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """PUT JSON with retry, rate limiting, and proxy rotation.

        Returns the parsed JSON body. The body may be a dict, list, or other
        JSON-compatible value depending on the endpoint.
        """
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                client = await self._get_client()
                merged_headers = {**self._default_headers, **(headers or {})}
                await self._acquire_request_slot()

                response = await client.put(
                    url,
                    json=json_body,
                    headers=merged_headers,
                )

                if response.status_code in _RETRYABLE_STATUS_CODES:
                    logger.warning(
                        "Retryable status %d from %s (attempt %d/%d)",
                        response.status_code, url, attempt + 1, self._max_retries + 1,
                    )
                    if attempt < self._max_retries:
                        self._rotate_proxy()
                        await asyncio.sleep(self._backoff_base * (2 ** attempt))
                        continue
                    response.raise_for_status()

                response.raise_for_status()
                return response.json()

            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
                last_error = exc
                logger.warning(
                    "Network error from %s: %s (attempt %d/%d)",
                    url, exc, attempt + 1, self._max_retries + 1,
                )
                if attempt < self._max_retries:
                    self._rotate_proxy()
                    await asyncio.sleep(self._backoff_base * (2 ** attempt))
                    continue

        raise last_error or RuntimeError(f"Failed after {self._max_retries + 1} attempts")

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
